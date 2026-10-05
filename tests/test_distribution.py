"""Wheel installs (uvx --from git+...) must carry the catalogue and seed the user data dir once."""

import tomllib
from pathlib import Path

from ema_rwe import config

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_bundles_every_seeded_file():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        include = tomllib.load(fh)["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for name in config.SEEDED_FILES:
        assert include[f"data/{name}"] == f"ema_rwe/data/{name}"
        assert (ROOT / "data" / name).is_file()


def test_wheel_bundles_the_caller_workflow_served_as_a_resource(tmp_path, monkeypatch):
    with (ROOT / "pyproject.toml").open("rb") as fh:
        include = tomllib.load(fh)["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert include[f"docs/{config.WORKFLOW_DOC}"] == f"ema_rwe/docs/{config.WORKFLOW_DOC}"
    assert config.workflow_doc_path() == ROOT / "docs" / config.WORKFLOW_DOC
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "no-checkout")  # an installed wheel
    assert config.workflow_doc_path() == Path(config.__file__).resolve().parent / "docs" / config.WORKFLOW_DOC


def test_checkout_uses_repo_data_dir():
    assert config.default_db_path() == ROOT / "data" / "ema.sqlite3"
    assert config.default_dictionary_dir() == ROOT / "data" / "dictionaries"


def test_wheel_install_seeds_user_data_dir_once(tmp_path, monkeypatch):
    bundled, user_dir = tmp_path / "bundled", tmp_path / "user"
    bundled.mkdir()
    for name in config.SEEDED_FILES:
        (bundled / name).write_text(f"bundled {name}")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "no-checkout")
    monkeypatch.setattr(config, "BUNDLED_DATA", bundled)
    monkeypatch.setattr(config, "user_data_path", lambda _app: user_dir)

    assert config.default_db_path() == user_dir / "ema.sqlite3"
    assert config.default_dictionary_dir() == user_dir / "dictionaries"
    assert not (user_dir / "terminology.json").exists()  # no disease dictionary ships with the wheel
    assert (user_dir / "ema-medicines.json").read_text() == "bundled ema-medicines.json"

    (user_dir / "ema.sqlite3").write_text("user edited")
    config.default_data_dir()
    assert (user_dir / "ema.sqlite3").read_text() == "user edited"


def catalogue(path, imported_at, *titles):
    from test_screening import study

    from ema_rwe.storage import Repository

    repo = Repository(path)
    for i, title in enumerate(titles, 1):
        repo.upsert(study(str(i), title=title))
    with repo.connection() as db:
        db.execute(
            "INSERT INTO imports VALUES (?,?,?,?)", (imported_at, "export.csv", imported_at, len(titles))
        )
    return repo


def test_newer_bundle_refreshes_catalogue_and_keeps_user_caches(tmp_path):
    from ema_rwe.storage import refresh_from_bundle

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    catalogue(bundled, "2026-10-01", "Pancreatitis cohort", "Newly registered stroke study")
    repo = catalogue(user, "2026-09-13", "Pancreatitis cohort")
    repo.observe("1", protocol_found=True, text_layer="none")  # the user's own observation
    type(repo)(bundled).observe("2", protocol_found=False)  # the bundle's backfill
    with repo.connection() as db:
        db.execute("INSERT INTO analyses VALUES ('1','fp',?)", ('{"analysis": {}}',))
        db.execute("CREATE TABLE protocol_answers (cache_key TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.execute("INSERT INTO protocol_answers VALUES ('k','answer')")

    assert refresh_from_bundle(bundled, user)
    refreshed = type(repo)(user)
    assert refreshed.study_count() == 2
    assert [r["study_id"] for r in refreshed.search("stroke", None, False, None)] == ["2"]
    assert refreshed.analysis("1") == {"analysis": {}}
    # Observations: the user's own survive the refresh, the bundle's are added
    assert refreshed.observation("1")["text_layer"] == "none"
    assert refreshed.observation("2")["protocol_found"] is False
    with refreshed.connection() as db:
        assert db.execute("SELECT body FROM protocol_answers").fetchone()[0] == "answer"
    assert not refresh_from_bundle(bundled, user)  # same import now: nothing to do


def test_user_import_newer_than_bundle_is_kept(tmp_path):
    from ema_rwe.storage import refresh_from_bundle

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    catalogue(bundled, "2026-09-13", "Old bundled study")
    catalogue(user, "2026-10-01", "User imported study")
    assert not refresh_from_bundle(bundled, user)
    assert refresh_from_bundle(bundled, tmp_path / "missing.sqlite3") is False


def test_bundle_with_another_schema_leaves_user_database_untouched(tmp_path):
    import sqlite3

    from ema_rwe.storage import refresh_from_bundle

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    catalogue(bundled, "2026-10-01", "Bundled study", "Another")
    db = sqlite3.connect(bundled)
    db.execute("PRAGMA user_version=999")
    db.close()
    repo = catalogue(user, "2026-09-13", "User study")
    assert not refresh_from_bundle(bundled, user)
    assert repo.study_count() == 1 and repo.get("1").title == "User study"


def test_bundle_observations_reach_a_user_whose_catalogue_is_current(tmp_path):
    from ema_rwe.storage import refresh_from_bundle

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    bundle = catalogue(bundled, "2026-10-01", "Beta blocker cohort", "Cohort")
    mine = catalogue(user, "2026-10-01", "Beta blocker cohort", "Cohort")
    mine.observe("1", text_layer="full")
    bundle.observe(
        "1", protocol_found=True, exposures=[{"term": "atenolol", "source": "protocol_pass_table"}]
    )
    bundle.observe("2", exposures=[{"term": "metoprolol", "source": "protocol_pass_table"}])
    # Same catalogue: nothing is replaced, but the bundle's backfill is merged
    assert not refresh_from_bundle(bundled, user)
    user_repo = type(mine)(user)
    assert user_repo.observation("1") == mine.observation("1")  # the user's own observation wins
    assert [r["study_id"] for r in user_repo.search("metoprolol", None, False, None)] == ["2"]


def test_shared_observations_carry_no_local_ids_or_errors(tmp_path):
    from ema_rwe.storage import refresh_from_bundle

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    bundle = catalogue(bundled, "2026-10-01", "Cohort")
    catalogue(user, "2026-10-01", "Cohort")
    bundle.observe(
        "1", protocol_found=True, protocol_id="pdf_1_x", backfill_error="PDF_PARSE_FAILED", backfill_done=True
    )
    refresh_from_bundle(bundled, user)
    observed = type(bundle)(user).observation("1")
    assert {"protocol_id", "backfill_error"}.isdisjoint(observed) and observed["backfill_done"] is True


def test_bundle_fills_missing_fields_of_an_existing_observation_and_cli_merge_is_strict(tmp_path):
    import pytest

    from ema_rwe.domain import RWEError
    from ema_rwe.storage import merge_bundle_observations

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    bundle = catalogue(bundled, "2026-10-01", "Cohort")
    mine = catalogue(user, "2026-10-01", "Cohort")
    mine.observe("1", text_layer="full")  # e.g. the user once retrieved this protocol
    bundle.observe("1", text_layer="none", exposures=[{"term": "atenolol", "source": "protocol_pass_table"}])
    assert merge_bundle_observations(bundled, user) == 1
    observed = type(mine)(user).observation("1")
    assert observed["text_layer"] == "full" and observed["exposures"][0]["term"] == "atenolol"
    with pytest.raises(RWEError):  # the CLI reports a wrong source instead of "0 added"
        merge_bundle_observations(tmp_path / "missing.sqlite3", user, strict=True)


def test_bundle_exposures_from_newer_rules_replace_only_that_source(tmp_path):
    """A parser fix reaches users who already merged the older bundle; their other sources stay."""
    from ema_rwe.storage import merge_bundle_observations

    bundled, user = tmp_path / "bundled.sqlite3", tmp_path / "user.sqlite3"
    bundle = catalogue(bundled, "2026-10-01", "Cohort (SONATA study)")
    mine = catalogue(user, "2026-10-01", "Cohort (SONATA study)")
    pass_table = {"term": "letrozole", "source": "protocol_pass_table"}
    mine.observe("1", exposures=[{"term": "sonatamab", "source": "catalogue_text"}, pass_table])
    bundle.observe("1", exposures=[], exposure_rules={"catalogue_text": 2})

    assert merge_bundle_observations(bundled, user) == 1
    observed = type(mine)(user).observation("1")
    assert observed["exposures"] == [pass_table] and observed["exposure_rules"] == {"catalogue_text": 2}
    assert not type(mine)(user).search("sonatamab", None, False, None, role="exposure")
    # Same rules on both sides: the local entries win again
    mine.observe("1", exposures=[pass_table, {"term": "anastrozole", "source": "catalogue_text"}])
    assert merge_bundle_observations(bundled, user) == 0


def test_vacuum_compacts_the_bundle_without_losing_rows(tmp_path):
    """The bundle build ends with VACUUM: free pages left by updates are dropped, rows are kept."""
    repo = catalogue(
        tmp_path / "bundle.sqlite3", "2026-10-01", *[f"Study {i} " + "x" * 2000 for i in range(200)]
    )
    with repo.connection() as db:
        db.execute("DELETE FROM studies WHERE id != '1'")
    sizes = repo.vacuum()
    assert sizes["bytes_after"] < sizes["bytes_before"]
    assert repo.get("1").title.startswith("Study 0")
