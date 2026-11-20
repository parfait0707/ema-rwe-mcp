"""Catalogue updates from the monthly snapshots published as GitHub releases of this project (spec v1.18;
plan: docs/research/202610081527_code_data_versioning_plan.md, stages 2 and 3).

A snapshot replaces the catalogue tables only: analyses, answers, readings, archived PDFs and unfinished
batches stay as they are, and a database whose own CSV import is newer is kept. The requests go to GitHub,
never to EMA, and never happen during a search: the server checks once at start (at most once a day), in a
thread of its own, and a user can run `ema-rwe update-catalogue` at any time.
"""

import json
import logging
import os
import re
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from platformdirs import user_data_path

from .config import REPO_ROOT, Settings
from .domain import Extraction, RWEError, atomic_write, now
from .pdf import PARSER_VERSION
from .storage import SCHEMA_VERSION, Repository, latest_import, refresh_from_bundle

log = logging.getLogger(__name__)

SNAPSHOT_TAG = re.compile(r"^data-(\d{8})$")
APP_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
PROJECT_URL = "git+https://github.com/parfait0707/ema-rwe-mcp"
EXPORT_DATE = re.compile(r"^(\d{8})_")
CHECK_INTERVAL = timedelta(hours=24)
MAX_MANIFEST_BYTES = 1 << 20
MAX_ARCHIVE_BYTES = 300 << 20
# Results that mean the catalogue is as good as it can be for now, and those that mean the attempt failed.
FAILED = {"unreachable", "rate_limited", "invalid_snapshot", "not_applied"}


def state_path(settings: Settings) -> Path:
    return settings.db_path.parent / "catalogue_update.json"


def read_state(settings: Settings) -> dict:
    try:
        state = json.loads(state_path(settings).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def current_snapshot(db_path: Path) -> str | None:
    """The date of the newest export the database was built from (YYYYMMDD, from the export file names);
    None when it has no dated import."""
    try:
        db = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            names = [row[0] or "" for row in db.execute("SELECT filename FROM imports")]
        finally:
            db.close()
    except sqlite3.Error:
        return None
    dates = [match[1] for name in names if (match := EXPORT_DATE.match(name))]
    return max(dates, default=None)


def automatic(settings: Settings) -> bool:
    """Whether the server may update this database on its own: only the database it seeded in the user data
    folder. One named with EMA_DB_PATH, or the committed one of a checkout, changes only on the user's command
    (the same rule as the refresh from a newer package's bundled catalogue)."""
    if settings.catalogue_update == "off" or os.getenv("EMA_DB_PATH"):
        return False
    if (REPO_ROOT / "pyproject.toml").is_file():
        return False
    return settings.db_path.resolve() == (user_data_path("ema-rwe-mcp") / "ema.sqlite3").resolve()


def status(settings: Settings) -> dict:
    """What catalogue_status reports about updates."""
    return {
        "mode": settings.catalogue_update,
        "automatic": automatic(settings),
        **read_state(settings),
    }


def versions(db_path: Path) -> dict:
    """What produced a result, recorded with it: when the same question gives another answer next month,
    the record shows whether the application, the catalogue or the PDF parser changed in between."""
    return {
        "app_version": version("ema-rwe-mcp"),
        "catalogue_snapshot": current_snapshot(db_path),
        "parser_version": PARSER_VERSION,
        "schema_version": Extraction.model_fields["schema_version"].default,
    }


def app_update(settings: Settings) -> dict | None:
    """A newer release of the application than the one running, as the last look at GitHub found it; None
    when this is the newest (or nothing is known). The application is never replaced by the server: the user
    changes the version their MCP client starts."""
    latest, running = read_state(settings).get("latest_app_version"), version("ema-rwe-mcp")
    if not latest or _version_tuple(latest) <= _version_tuple(running):
        return None
    return {
        "current": running,
        "latest": latest,
        "instruction": f"Tell the user that ema-rwe-mcp {latest} is available (this is {running}). To update: "
        f"where the MCP client's configuration pins a version ('{PROJECT_URL}@v{running}'), change it to "
        f"'@v{latest}' and restart the client; an unpinned 'uvx --from {PROJECT_URL} ema-rwe-mcp' keeps "
        f"the version uv cached until it is run once with 'uvx --refresh --from {PROJECT_URL} ema-rwe-mcp'. "
        "Saved analyses, answers and PDFs are kept.",
    }


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def _get(client: httpx.Client, url: str, limit: int, target: Path | None = None) -> bytes:
    """GET within a size limit; GitHub only, HTTPS only, also after redirects. With target, stream to it."""
    with client.stream("GET", url, follow_redirects=True) as response:
        for hop in [*response.history, response]:
            parts = urlsplit(str(hop.url))
            host = parts.hostname or ""
            if parts.scheme != "https" or not (
                host in ("github.com", "api.github.com") or host.endswith(".githubusercontent.com")
            ):
                raise RWEError("SNAPSHOT_INVALID", f"Snapshot request left GitHub ({host}).")
        if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
            raise _Failure("rate_limited", "GitHub's request limit for this address is used up for now.")
        if response.status_code >= 400:
            raise _Failure("unreachable", f"GitHub answered HTTP {response.status_code}.")
        received, parts_read, sink = 0, [], target.open("wb") if target else None
        try:
            for block in response.iter_bytes():
                received += len(block)
                if received > limit:
                    raise RWEError("SNAPSHOT_INVALID", f"Response exceeds {limit} bytes.")
                sink.write(block) if sink else parts_read.append(block)
        finally:
            if sink:
                sink.close()
        return b"".join(parts_read)


class _Failure(Exception):
    def __init__(self, result: str, reason: str):
        super().__init__(reason)
        self.result, self.reason = result, reason


def published(client: httpx.Client, url: str) -> list[dict]:
    """The project's published releases (no drafts, no prereleases)."""
    releases = json.loads(_get(client, url, MAX_MANIFEST_BYTES * 4))
    return [
        r
        for r in (releases if isinstance(releases, list) else [])
        if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")
    ]


def latest_app(releases: list[dict]) -> str | None:
    """The highest vX.Y.Z among the releases, or None."""
    found = [match[1] for r in releases if (match := APP_TAG.match(str(r.get("tag_name", ""))))]
    return max(found, key=_version_tuple, default=None)


def latest_release(releases: list[dict]) -> dict | None:
    """The newest data-YYYYMMDD release with its manifest and archive links, or None."""
    found = []
    for release in releases:
        match = SNAPSHOT_TAG.match(str(release.get("tag_name", "")))
        if not match:
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
        archive = next((link for name, link in assets.items() if str(name).endswith(".sqlite3.gz")), None)
        if assets.get("manifest.json") and archive:
            found.append(
                {"snapshot": match[1], "manifest_url": assets["manifest.json"], "archive_url": archive}
            )
    return max(found, key=lambda r: r["snapshot"], default=None)


def update_catalogue(
    settings: Settings,
    apply: bool = True,
    archive: Path | None = None,
    manifest: Path | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Look for a newer snapshot and, with apply, verify it and replace the catalogue tables with it.

    archive and manifest: a snapshot the user downloaded by hand (no request is made). Returns what happened
    (`result`, and `reason` when nothing changed) and records it for catalogue_status; it does not raise for
    an unreachable GitHub or a refused snapshot, since the working catalogue is still there."""
    from .snapshot import verify_snapshot  # snapshot imports the service; keep this module light

    if (archive is None) != (manifest is None):
        raise RWEError("INVALID_INPUT", "Give the snapshot archive and its manifest.json together.")
    Repository(settings.db_path)  # a database to update, at this package's schema
    outcome = {"checked_at": now(), "current_snapshot": current_snapshot(settings.db_path)}
    own = client is None
    client = client or httpx.Client(
        timeout=settings.timeout, headers={"User-Agent": settings.user_agent, "Accept": "application/json"}
    )
    try:
        with tempfile.TemporaryDirectory(dir=settings.db_path.parent) as folder:
            if archive:
                described, packed = json.loads(manifest.read_text(encoding="utf-8")), archive
                outcome["latest_snapshot"] = str(described.get("snapshot_id"))
            else:
                releases = published(client, settings.catalogue_releases_url)
                if newest := latest_app(releases):  # the same list also tells of a newer application
                    outcome["latest_app_version"] = newest
                release = latest_release(releases)
                if release is None:
                    return _record(settings, outcome, "no_snapshot", "No catalogue snapshot is published.")
                outcome["latest_snapshot"] = release["snapshot"]
            current = outcome["current_snapshot"]
            if current and outcome["latest_snapshot"] <= current:
                return _record(settings, outcome, "up_to_date")
            if not archive:
                described = json.loads(_get(client, release["manifest_url"], MAX_MANIFEST_BYTES))
                if str(described.get("snapshot_id")) != release["snapshot"]:
                    raise RWEError(
                        "SNAPSHOT_INVALID", "The manifest describes another snapshot than its release."
                    )
            needs = _version_tuple(str(described.get("min_app_version", "0")))
            if described.get("schema_version") != SCHEMA_VERSION or needs > _version_tuple(
                version("ema-rwe-mcp")
            ):
                return _record(
                    settings,
                    outcome,
                    "app_update_required",
                    f"Snapshot {outcome['latest_snapshot']} needs a newer ema-rwe-mcp (schema "
                    f"{described.get('schema_version')}, from version {described.get('min_app_version')}); this "
                    f"is {version('ema-rwe-mcp')} with schema {SCHEMA_VERSION}.",
                )
            if not apply:
                return _record(settings, outcome, "available", "A newer snapshot is published; not applied.")
            if not archive:
                packed = Path(folder) / "snapshot.sqlite3.gz"
                expected = int((described.get("compressed") or {}).get("size") or MAX_ARCHIVE_BYTES)
                _get(client, release["archive_url"], min(expected, MAX_ARCHIVE_BYTES), packed)
            unpacked = verify_snapshot(described, packed, Path(folder) / "snapshot.sqlite3")
            mine, theirs = latest_import(settings.db_path), latest_import(unpacked)
            if mine and theirs and mine >= theirs:
                return _record(
                    settings,
                    outcome,
                    "kept_local_import",
                    "A CSV imported into this database is newer than the snapshot; the catalogue was kept.",
                )
            if not refresh_from_bundle(unpacked, settings.db_path):
                return _record(
                    settings, outcome, "not_applied", "The catalogue tables could not be replaced."
                )
            outcome.update(applied_at=now(), applied_snapshot=outcome["latest_snapshot"])
            outcome["current_snapshot"] = current_snapshot(settings.db_path)
            return _record(settings, outcome, "updated")
    except _Failure as failure:
        return _record(settings, outcome, failure.result, failure.reason)
    except httpx.HTTPError as exc:
        return _record(
            settings, outcome, "unreachable", f"GitHub could not be reached ({type(exc).__name__})."
        )
    except RWEError as exc:
        if exc.code != "SNAPSHOT_INVALID":
            raise
        return _record(settings, outcome, "invalid_snapshot", exc.message)
    except (ValueError, KeyError, TypeError) as exc:
        return _record(settings, outcome, "invalid_snapshot", f"Unreadable release or manifest ({exc}).")
    finally:
        if own:
            client.close()


def _record(settings: Settings, outcome: dict, result: str, reason: str | None = None) -> dict:
    previous = read_state(settings)
    state = {
        # What was applied last stays visible while later checks find nothing new.
        **{k: previous[k] for k in ("applied_at", "applied_snapshot", "latest_app_version") if k in previous},
        **outcome,
        "result": result,
        **({"reason": reason} if reason else {}),
    }
    try:
        atomic_write(state_path(settings), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass  # a read-only folder: the result is still returned
    log.info("catalogue_update result=%s snapshot=%s", result, outcome.get("latest_snapshot"))
    return state


def due(settings: Settings) -> bool:
    """No check in the last day (a failed attempt counts: GitHub is asked at most once a day)."""
    checked = read_state(settings).get("checked_at")
    try:
        return not checked or datetime.now(UTC) - datetime.fromisoformat(checked) >= CHECK_INTERVAL
    except (TypeError, ValueError):
        return True


def startup_update(settings: Settings, client: httpx.Client | None = None) -> dict | None:
    """The server's own check at start: nothing when updates are off for this database or one ran today.
    Never raises; the server works with the catalogue it has."""
    try:
        if not automatic(settings) or not due(settings):
            return None
        return update_catalogue(settings, apply=settings.catalogue_update == "apply", client=client)
    except Exception:
        log.exception("catalogue_update failed")
        return None
