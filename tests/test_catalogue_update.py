"""Catalogue updates from published snapshots (spec v1.18): GitHub is mocked, the snapshot is a real one
built from the test exports."""

import json

import httpx
import pytest
from test_snapshot import exports  # noqa: F401  (fixture)

from ema_rwe import catalogue_update
from ema_rwe.catalogue_update import (
    automatic,
    current_snapshot,
    read_state,
    startup_update,
    update_catalogue,
)
from ema_rwe.config import Settings
from ema_rwe.service import Service
from ema_rwe.snapshot import build_snapshot
from ema_rwe.storage import Repository, import_csv

RELEASE = "https://github.com/parfait0707/ema-rwe-mcp/releases/download/data-20261101"


@pytest.fixture
def snapshot(exports, tmp_path):  # noqa: F811
    manifest = build_snapshot(exports, "20261101", tmp_path / "out", None)
    return manifest, tmp_path / "out" / manifest["compressed"]["name"]


class GitHub:
    """A stand-in for the release list, the manifest (served through a redirect, as GitHub does) and the
    archive. `seen` lists the paths asked for."""

    def __init__(self, manifest: dict, archive: bytes):
        self.manifest, self.archive, self.seen = manifest, archive, []
        self.releases = [
            {"tag_name": "v0.5.7", "draft": False, "prerelease": False, "assets": []},
            {"tag_name": "data-20261001", "draft": False, "prerelease": False, "assets": []},  # no files
            {
                "tag_name": "data-20261101",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {"name": "manifest.json", "browser_download_url": f"{RELEASE}/manifest.json"},
                    {
                        "name": manifest["compressed"]["name"],
                        "browser_download_url": f"{RELEASE}/snapshot.gz",
                    },
                ],
            },
            {"tag_name": "data-20991231", "draft": True, "prerelease": False, "assets": []},
        ]
        self.redirect = "https://release-assets.githubusercontent.com/files/manifest"
        self.fail = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.url.path)
        if self.fail:
            return self.fail(request)
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=self.releases)
        if request.url.path.endswith("/manifest.json"):
            return httpx.Response(302, headers={"location": self.redirect})
        if request.url.path == "/files/manifest":
            return httpx.Response(200, json=self.manifest)
        return httpx.Response(200, content=self.archive)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def github(snapshot):
    manifest, packed = snapshot
    return GitHub(manifest, packed.read_bytes())


def test_newer_snapshot_replaces_the_catalogue_and_keeps_the_users_work(settings, github, snapshot):
    repo = Repository(settings.db_path)
    with repo.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS protocol_answers (cache_key TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        db.execute("INSERT INTO protocol_answers VALUES ('mine', '{}')")
    assert repo.study_count() == 0 and current_snapshot(settings.db_path) is None
    result = update_catalogue(settings, client=github.client())
    assert result["result"] == "updated" and result["applied_snapshot"] == "20261101"
    assert result["current_snapshot"] == "20261101" == current_snapshot(settings.db_path)
    assert repo.study_count() == snapshot[0]["studies_total"] > 0
    with repo.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM protocol_answers").fetchone()[0] == 1
    # The draft release and the release without files were not candidates
    assert read_state(settings)["latest_snapshot"] == "20261101"
    status = Service(settings).catalogue_status()
    assert status["catalogue_snapshot"] == "20261101" and status["update"]["result"] == "updated"
    assert status["update"]["mode"] == "apply" and status["update"]["automatic"] is False
    # Asking again costs one small request and downloads nothing
    github.seen.clear()
    again = update_catalogue(settings, client=github.client())
    assert again["result"] == "up_to_date" and len(github.seen) == 1
    assert again["applied_snapshot"] == "20261101"  # what was applied stays on record


def test_check_reports_a_newer_snapshot_without_taking_it(settings, github):
    result = update_catalogue(settings, apply=False, client=github.client())
    assert result["result"] == "available" and result["latest_snapshot"] == "20261101"
    assert Repository(settings.db_path).study_count() == 0
    assert not any(path.endswith("snapshot.gz") for path in github.seen)


def test_a_snapshot_that_fails_verification_leaves_the_catalogue_alone(settings, github):
    github.archive = github.archive[:-200] + b"x" * 200
    result = update_catalogue(settings, client=github.client())
    assert result["result"] == "invalid_snapshot" and "checksum" in result["reason"]
    assert Repository(settings.db_path).study_count() == 0
    assert not list(settings.db_path.parent.glob("tmp*"))  # nothing of the download is left behind


def test_a_snapshot_for_a_newer_package_asks_for_an_application_update(settings, github):
    github.manifest = {**github.manifest, "min_app_version": "99.0.0"}
    result = update_catalogue(settings, client=github.client())
    assert result["result"] == "app_update_required" and "99.0.0" in result["reason"]
    assert not any(path.endswith("snapshot.gz") for path in github.seen)
    github.manifest = {**github.manifest, "min_app_version": "0.5.5", "schema_version": 999}
    assert update_catalogue(settings, client=github.client())["result"] == "app_update_required"


def test_an_unreachable_or_rate_limited_github_is_reported_not_raised(settings, github):
    def offline(request):
        raise httpx.ConnectError("no route", request=request)

    github.fail = offline
    assert update_catalogue(settings, client=github.client())["result"] == "unreachable"
    github.fail = lambda request: httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    assert update_catalogue(settings, client=github.client())["result"] == "rate_limited"
    github.fail = lambda request: httpx.Response(500)
    result = update_catalogue(settings, client=github.client())
    assert result["result"] == "unreachable" and "500" in result["reason"]
    assert read_state(settings)["result"] == "unreachable"


def test_a_download_that_leaves_github_is_refused(settings, github):
    github.redirect = "https://example.org/manifest"
    result = update_catalogue(settings, client=github.client())
    assert result["result"] == "invalid_snapshot" and "example.org" in result["reason"]
    github.releases = {"message": "not a list"}
    assert update_catalogue(settings, client=github.client())["result"] == "no_snapshot"


def test_a_csv_the_user_imported_after_the_snapshot_was_built_is_kept(settings, github, csv_file):
    imported = import_csv(Repository(settings.db_path), csv_file)  # now: later than the snapshot's imports
    assert imported["imported"] > 0
    result = update_catalogue(settings, client=github.client())
    assert result["result"] == "kept_local_import"
    assert current_snapshot(settings.db_path) is None  # export-data.csv carries no date: still the user's own


def test_a_snapshot_downloaded_by_hand_is_applied_without_a_request(settings, github, snapshot, tmp_path):
    manifest, packed = snapshot
    described = tmp_path / "out" / "manifest.json"
    result = update_catalogue(settings, archive=packed, manifest=described, client=github.client())
    assert result["result"] == "updated" and github.seen == []
    assert Repository(settings.db_path).study_count() == manifest["studies_total"]
    from ema_rwe.domain import RWEError

    with pytest.raises(RWEError):
        update_catalogue(settings, archive=packed, client=github.client())  # the manifest is required


def test_the_server_updates_only_its_own_seeded_database_and_once_a_day(settings, github, monkeypatch):
    # A database named by the user (as every test database is) is never updated on the server's initiative
    assert automatic(settings) is False
    assert startup_update(settings, client=github.client()) is None and github.seen == []
    monkeypatch.setattr(catalogue_update, "automatic", lambda _settings: True)
    first = startup_update(settings, client=github.client())
    assert first["result"] == "updated"
    github.seen.clear()
    assert startup_update(settings, client=github.client()) is None and github.seen == []  # checked today
    # A failed attempt also waits for the next day
    monkeypatch.setattr(catalogue_update, "due", lambda _settings: True)
    github.fail = lambda request: httpx.Response(500)
    assert startup_update(settings, client=github.client())["result"] == "unreachable"


def test_check_mode_only_reports_and_off_does_nothing(settings, github, monkeypatch, tmp_path):
    settings.catalogue_update = "check"
    monkeypatch.setattr(catalogue_update, "automatic", lambda s: s.catalogue_update != "off")
    assert startup_update(settings, client=github.client())["result"] == "available"
    assert Repository(settings.db_path).study_count() == 0
    monkeypatch.undo()
    settings.catalogue_update = "off"
    assert automatic(settings) is False
    monkeypatch.setenv("EMA_DB_PATH", str(tmp_path / "named.sqlite3"))
    assert automatic(Settings(cache_dir=tmp_path / "http")) is False
    monkeypatch.setenv("EMA_CATALOGUE_UPDATE", "sometimes")
    with pytest.raises(ValueError, match="EMA_CATALOGUE_UPDATE"):
        Settings(cache_dir=tmp_path / "http")


def test_a_broken_state_file_or_database_does_not_stop_the_check(settings, github, monkeypatch):
    catalogue_update.state_path(settings).parent.mkdir(parents=True, exist_ok=True)
    catalogue_update.state_path(settings).write_text("{not json")
    assert read_state(settings) == {} and catalogue_update.due(settings) is True
    monkeypatch.setattr(catalogue_update, "automatic", lambda _settings: True)

    def explode(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(catalogue_update, "update_catalogue", explode)
    assert startup_update(settings, client=github.client()) is None  # logged, never raised


def test_state_is_plain_json_beside_the_database(settings, github):
    update_catalogue(settings, apply=False, client=github.client())
    state = json.loads(catalogue_update.state_path(settings).read_text(encoding="utf-8"))
    assert set(state) >= {"checked_at", "current_snapshot", "latest_snapshot", "result", "reason"}
