import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from ema_rwe.domain import RWEError
from ema_rwe.ema import BASE
from ema_rwe.http import EMAClient, retry_delay, safe_url


@pytest.mark.parametrize(
    "url",
    [
        "http://catalogues.ema.europa.eu/x",
        "https://example.org/x",
        "https://catalogues.ema.europa.eu.evil.test/",
        "https://user@catalogues.ema.europa.eu/",
        "file:///etc/passwd",
    ],
)
def test_url_allowlist(url):
    with pytest.raises(RWEError):
        safe_url(url)


def test_retry_after():
    assert retry_delay("17", 0) == 17
    assert retry_delay("broken", 1) == 5
    assert 27 <= retry_delay(format_datetime(datetime.now(UTC) + timedelta(seconds=30)), 0) <= 30


async def test_retry_cache_and_expiration(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /search/")
        if calls.count("/document") == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"payload")

    client = EMAClient(settings, httpx.MockTransport(handler))
    try:
        _, a = await client.get(BASE + "/document")
        _, b = await client.get(BASE + "/document")
        assert a["cached"] is False and b["cached"] is True
        assert calls.count("/document") == 2
        key = hashlib.sha256((BASE + "/document").encode()).hexdigest()
        manifest = settings.cache_dir / (key + ".json")
        meta = json.loads(manifest.read_text())
        meta["expires_at"] = time.time() - 1
        manifest.write_text(json.dumps(meta))
        client.cleanup()
        assert not manifest.exists()
        assert not manifest.with_suffix(".bin").exists()
        await client.get(BASE + "/document")
        assert calls.count("/document") == 3
        with pytest.raises(RWEError, match="robots"):
            await client.get(BASE + "/search/export-data-study")
    finally:
        await client.close()


async def test_redirect_external_blocked(settings):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secrets"})

    client = EMAClient(settings, httpx.MockTransport(handler))
    try:
        with pytest.raises(RWEError, match="Only HTTPS"):
            await client.get(BASE + "/document")
    finally:
        await client.close()


async def test_large_retry_after_returns_actionable_error(settings):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(429, headers={"Retry-After": "3600"})

    client = EMAClient(settings, httpx.MockTransport(handler))
    try:
        with pytest.raises(RWEError, match="3600") as error:
            await client.get(BASE + "/document")
        assert error.value.code == "EMA_RATE_LIMITED"
    finally:
        await client.close()
