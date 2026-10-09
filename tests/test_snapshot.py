"""Monthly catalogue snapshots: built from one refresh's four exports, verified before use (plan stage 1)."""

import gzip
import json

import pytest

from ema_rwe.domain import RWEError
from ema_rwe.snapshot import build_snapshot, sha256_file, snapshot_exports, verify_snapshot
from ema_rwe.storage import Repository, refresh_from_bundle


@pytest.fixture
def exports(tmp_path, csv_file):
    """The four exports of refresh 20261101. Types overlap, so two typed exports (and a typed and the full
    export) may be byte-identical: claims equals the full export and ehr equals registry here."""
    root = tmp_path / "imports"
    (root / "studies").mkdir(parents=True)
    (root / "source_type").mkdir()
    lines = csv_file.read_text(encoding="utf-8-sig").splitlines()
    for folder, kind, body in [
        ("studies", "all", lines),
        ("source_type", "claims", lines),
        ("source_type", "ehr", lines[:2]),
        ("source_type", "registry", lines[:2]),
    ]:
        (root / folder / f"20261101_{kind}_export-data.csv").write_text("\n".join(body), encoding="utf-8")
    return root


def test_snapshot_is_built_from_the_four_exports_of_one_refresh(exports, tmp_path):
    manifest = build_snapshot(exports, "20261101", tmp_path / "out", None)
    assert [e["kind"] for e in manifest["exports"]] == ["studies", "claims", "ehr", "registry"]
    # Byte-identical exports of different kinds each report their own row count
    rows = {e["kind"]: e["rows"] for e in manifest["exports"]}
    assert rows["studies"] == rows["claims"] > rows["ehr"] == rows["registry"] > 0
    assert (
        manifest["studies_total"] == Repository(tmp_path / "out" / manifest["database"]["name"]).study_count()
    )
    assert json.loads((tmp_path / "out" / "manifest.json").read_text()) == manifest
    # The snapshot replaces an older catalogue through the same path as the bundled database
    unpacked = verify_snapshot(
        manifest, tmp_path / "out" / manifest["compressed"]["name"], tmp_path / "x.sqlite3"
    )
    user = tmp_path / "user.sqlite3"
    Repository(user)
    assert refresh_from_bundle(unpacked, user) is True
    assert Repository(user).study_count() == manifest["studies_total"]


def test_a_refresh_missing_a_kind_or_mixing_dates_is_refused(exports):
    (exports / "source_type" / "20261101_registry_export-data.csv").rename(
        exports / "source_type" / "20261001_registry_export-data.csv"
    )
    with pytest.raises(RWEError, match="registry: 0 files"):
        snapshot_exports(exports, "20261101")


def test_a_tampered_or_foreign_snapshot_is_refused_and_left_unpacked_nowhere(exports, tmp_path):
    manifest = build_snapshot(exports, "20261101", tmp_path / "out", None)
    packed = tmp_path / "out" / manifest["compressed"]["name"]
    target = tmp_path / "unpacked.sqlite3"
    with pytest.raises(RWEError, match="schema"):
        verify_snapshot(dict(manifest, schema_version=manifest["schema_version"] + 1), packed, target)
    bad = tmp_path / "bad.gz"
    bad.write_bytes(gzip.compress(b"not a database"))
    with pytest.raises(RWEError, match="checksum"):
        verify_snapshot(manifest, bad, target)
    forged = dict(
        manifest,
        compressed=dict(manifest["compressed"], sha256=sha256_file(bad)),
    )
    with pytest.raises(RWEError, match="checksum"):
        verify_snapshot(forged, bad, target)
    assert not target.exists()
    with pytest.raises(RWEError, match="lacks"):
        verify_snapshot({k: v for k, v in manifest.items() if k != "compressed"}, packed, target)


def test_the_manifest_counts_observations_of_the_snapshot_studies_only(exports, tmp_path):
    """Observations merged for studies outside the snapshot's exports are not counted."""
    observations = tmp_path / "observations.sqlite3"
    repo = Repository(observations)
    repo.observe("123", protocol_found=True, exposures=[{"term": "apixaban", "source": "catalogue_text"}])
    repo.observe("456", protocol_found=True)  # checked, no medicines found
    repo.observe("999999", protocol_found=True, exposures=[{"term": "x", "source": "catalogue_text"}])
    manifest = build_snapshot(exports, "20261101", tmp_path / "out", observations)
    assert manifest["observed_studies"] == 2 and manifest["backfilled_studies"] == 1


def test_the_script_verifies_a_downloaded_snapshot_folder(exports, tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "build_catalogue_snapshot.py"
    manifest = build_snapshot(exports, "20261101", tmp_path / "out", None)
    ok = subprocess.run(
        [sys.executable, script, "--verify", tmp_path / "out"], capture_output=True, text=True, check=False
    )
    assert ok.returncode == 0 and json.loads(ok.stdout)["verified"] == "20261101"
    broken = dict(manifest)
    del broken["studies_total"]
    (tmp_path / "out" / "manifest.json").write_text(json.dumps(broken))
    bad = subprocess.run(
        [sys.executable, script, "--verify", tmp_path / "out"], capture_output=True, text=True, check=False
    )
    assert bad.returncode == 1 and "SNAPSHOT_INVALID" in bad.stderr
