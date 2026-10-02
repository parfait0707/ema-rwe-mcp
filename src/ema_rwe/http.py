import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import Settings
from .domain import RWEError, atomic_write
from .ema import BASE

log = logging.getLogger(__name__)


def safe_url(url: str):
    u = urlsplit(url)
    if (
        u.scheme != "https"
        or u.hostname != "catalogues.ema.europa.eu"
        or u.port not in (None, 443)
        or u.username
        or u.password
    ):
        raise RWEError("INVALID_URL", "Only HTTPS EMA Catalogue URLs are accepted.")


def retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError):
                pass
    return (2, 5, 15)[min(attempt, 2)]


class EMAClient:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.cache = settings.cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(
            timeout=settings.timeout,
            transport=transport,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=False,
        )
        self.lock = asyncio.Lock()
        self.last_request = 0.0
        self.requests_made = 0  # actual HTTP requests (cache hits excluded), for honest network counts
        self.robots = None
        self.cleanup()

    async def close(self):
        await self.client.aclose()

    def cleanup(self) -> int:
        removed = 0
        for path in self.cache.glob("*.json"):
            try:
                if json.loads(path.read_text())["expires_at"] > time.time():
                    continue
            except (ValueError, KeyError):
                pass
            path.with_suffix(".bin").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            removed += 1
        for path in self.cache.glob("*.bin"):
            if (
                not path.with_suffix(".json").exists()
                and path.stat().st_mtime + self.settings.ttl < time.time()
            ):
                path.unlink(missing_ok=True)
        for path in self.cache.glob("*.tmp"):
            if path.stat().st_mtime + self.settings.ttl < time.time():
                path.unlink(missing_ok=True)
        return removed

    async def get(self, url: str, refresh: bool = False) -> tuple[bytes, dict]:
        safe_url(url)
        async with self.lock:
            if self.robots is None:
                content, _ = await self._get(BASE + "/robots.txt", False, check_robots=False)
                self.robots = RobotFileParser()
                self.robots.parse(content.decode("utf-8", errors="replace").splitlines())
            return await self._get(url, refresh)

    async def _get(self, url: str, refresh: bool, check_robots: bool = True):
        if check_robots and not self.robots.can_fetch(self.settings.user_agent, url):
            raise RWEError("ROBOTS_DISALLOWED", "EMA robots.txt disallows this path; use manual CSV export.")
        key = hashlib.sha256(url.encode()).hexdigest()
        manifest, body = self.cache / (key + ".json"), self.cache / (key + ".bin")
        if not refresh and manifest.exists() and body.exists():
            try:
                meta = json.loads(manifest.read_text())
                data = body.read_bytes()
                if meta["expires_at"] > time.time() and hashlib.sha256(data).hexdigest() == meta["sha256"]:
                    return data, dict(meta, cached=True)
            except (ValueError, KeyError, OSError):
                pass
        # Expired raw content is removed even when the next request fails.
        body.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        current = url
        for redirect in range(6):
            safe_url(current)
            if check_robots and not self.robots.can_fetch(self.settings.user_agent, current):
                raise RWEError("ROBOTS_DISALLOWED", "Redirect target disallowed by robots.txt.")
            for attempt in range(4):
                await asyncio.sleep(max(0, self.settings.interval - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                self.requests_made += 1
                try:
                    async with self.client.stream("GET", current) as response:
                        status = response.status_code
                        log.info("ema_http status=%s attempt=%s", status, attempt)
                        if status in (408, 429, 500, 502, 503, 504):
                            delay = retry_delay(response.headers.get("Retry-After"), attempt)
                            if attempt == 3 or delay > 60:
                                raise RWEError(
                                    "EMA_RATE_LIMITED" if status == 429 else "EMA_UNAVAILABLE",
                                    f"HTTP {status}; retry after {delay:.0f} seconds.",
                                )
                        elif status in (301, 302, 303, 307, 308):
                            current = urljoin(current, response.headers.get("location", ""))
                            break
                        elif status >= 400:  # not retried: the page or document is gone or refused
                            raise RWEError("EMA_HTTP_ERROR", f"EMA returned HTTP {status}.")
                        else:
                            parts, length = [], 0
                            async for chunk in response.aiter_bytes():
                                length += len(chunk)
                                if length > 50 * 1024 * 1024:
                                    raise RWEError(
                                        "DOCUMENT_DOWNLOAD_FAILED", "Response exceeds 50 MiB limit."
                                    )
                                parts.append(chunk)
                            data = b"".join(parts)
                            meta = {
                                "document_url": url,
                                "final_url": current,
                                "retrieved_at": datetime.now(UTC).isoformat(),
                                "expires_at": time.time() + self.settings.ttl,
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "content_type": response.headers.get("content-type", ""),
                            }
                            # Atomic replace; callers never observe a half-written body/manifest.
                            atomic_write(body, data)
                            atomic_write(manifest, json.dumps(meta))
                            return data, dict(meta, cached=False)
                except httpx.TransportError as exc:
                    if attempt == 3:
                        raise RWEError(
                            "EMA_UNAVAILABLE", "EMA network request failed after retries."
                        ) from exc
                    delay = retry_delay(None, attempt)
                await asyncio.sleep(delay)
            else:
                raise RWEError("EMA_UNAVAILABLE", "EMA retry limit exceeded.")
        raise RWEError("EMA_UNAVAILABLE", "Too many redirects.")
