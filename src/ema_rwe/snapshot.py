"""Monthly catalogue snapshots: one database built from the four exports of one refresh, verified and
published apart from the code (docs/research/202610081527_code_data_versioning_plan.md, stage 1).

The maintainer builds a snapshot with scripts/build_catalogue_snapshot.py; a server will verify it with
verify_snapshot before replacing its catalogue tables (stage 2).
"""

import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from .config import Settings
from .domain import RWEError
from .service import Service, source_type_from_filename
from .storage import SCHEMA_VERSION, Repository, merge_bundle_observations

MANIFEST_VERSION = 1
KINDS = ("studies", "claims", "ehr", "registry")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_exports(import_dir: Path, snapshot_id: str) -> dict[str, Path]:
    """The four exports of one refresh, named with its date: studies/<id>_*.csv and
    source_type/<id>_<claims|ehr|registry>_*.csv. A missing or duplicated kind is an error, so a snapshot
    never mixes refreshes or lacks the source-type tags."""
    found: dict[str, list[Path]] = {kind: [] for kind in KINDS}
    for path in sorted((import_dir / "studies").glob(f"{snapshot_id}_*.csv")):
        found["studies"].append(path)
    for path in sorted((import_dir / "source_type").glob(f"{snapshot_id}_*.csv")):
        if kind := source_type_from_filename(path.name):
            found[kind].append(path)
    problems = [f"{kind}: {len(paths)} files" for kind, paths in found.items() if len(paths) != 1]
    if problems:
        raise RWEError("SNAPSHOT_INVALID", f"Need one export of each kind dated {snapshot_id}: {problems}.")
    return {kind: paths[0] for kind, paths in found.items()}


def check_database(path: Path) -> None:
    """The file is an intact catalogue database of this package's schema."""
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RWEError("SNAPSHOT_INVALID", "Snapshot database failed PRAGMA integrity_check.")
        if (schema := db.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            raise RWEError(
                "SNAPSHOT_INVALID", f"Snapshot schema {schema}; this package reads {SCHEMA_VERSION}."
            )
        studies = db.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
        indexed = db.execute("SELECT COUNT(*) FROM study_fts").fetchone()[0]
        if not studies or studies != indexed:
            raise RWEError("SNAPSHOT_INVALID", f"{studies} studies but {indexed} search index rows.")
    except sqlite3.Error as exc:
        raise RWEError("SNAPSHOT_INVALID", f"Snapshot database unreadable: {exc}") from exc
    finally:
        db.close()


def build_snapshot(import_dir: Path, snapshot_id: str, out_dir: Path, observations_from: Path | None) -> dict:
    """Build ema-catalogue-<id>.sqlite3 and .sqlite3.gz plus manifest.json in out_dir; return the manifest.

    Only the four exports of the refresh are imported (into a fresh database, so no earlier export
    leaks in). Observations (the backfill of medicines from protocols) come from observations_from,
    usually the previous snapshot or the committed database."""
    exports = snapshot_exports(import_dir, snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"ema-catalogue-{snapshot_id}.sqlite3"
    database, packed = out_dir / name, out_dir / f"{name}.gz"
    if database.exists() or packed.exists():
        raise RWEError("SNAPSHOT_INVALID", f"{out_dir} already holds snapshot {snapshot_id}.")
    with tempfile.TemporaryDirectory(dir=out_dir) as work:
        staging = Path(work) / "imports"
        for kind, path in exports.items():
            folder = staging / ("studies" if kind == "studies" else "source_type")
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, folder / path.name)
        building = Path(work) / name
        settings = Settings(db_path=building, cache_dir=Path(work) / "http", import_dir=staging)
        Service(settings).import_all()
        if observations_from:
            merge_bundle_observations(observations_from, building, strict=True)
        Repository(building).vacuum()
        check_database(building)
        shutil.move(building, database)
    with database.open("rb") as source, gzip.GzipFile(packed, "wb", mtime=0) as target:
        shutil.copyfileobj(source, target)  # mtime=0: the same database always packs to the same bytes
    db = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        studies = db.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
        # Observations of this snapshot's studies: every one checked, and those given medicines (backfill)
        observed, backfilled = db.execute(
            "SELECT COUNT(*), SUM(COALESCE(json_array_length(body, '$.exposures'), 0) > 0) "
            "FROM protocol_observations WHERE study_id IN (SELECT id FROM studies)"
        ).fetchone()
    finally:
        db.close()
    rows = {r["filename"]: r["count"] for r in Repository(database).imports()}
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(UTC).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "built_with": version("ema-rwe-mcp"),
        # The oldest package that reads it: the schema is the only contract, so the builder's version.
        "min_app_version": version("ema-rwe-mcp"),
        "database": {"name": name, "sha256": sha256_file(database), "size": database.stat().st_size},
        "compressed": {"name": packed.name, "sha256": sha256_file(packed), "size": packed.stat().st_size},
        "exports": [
            {"kind": kind, "filename": path.name, "rows": rows[path.name], "sha256": sha256_file(path)}
            for kind, path in exports.items()
        ],
        "studies_total": studies,
        "observed_studies": observed,
        "backfilled_studies": backfilled or 0,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def verify_snapshot(manifest: dict, packed: Path, target: Path) -> Path:
    """Unpack a downloaded snapshot to target after checking it against its manifest: format, schema,
    both checksums and the database itself. Raises SNAPSHOT_INVALID and leaves no target on failure."""
    if not all(
        isinstance(manifest.get(k), dict) and "sha256" in manifest[k] for k in ("database", "compressed")
    ):
        raise RWEError("SNAPSHOT_INVALID", "Manifest lacks the database and compressed checksums.")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise RWEError("SNAPSHOT_INVALID", f"Unknown manifest version {manifest.get('manifest_version')}.")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RWEError(
            "SNAPSHOT_INVALID", f"Snapshot schema {manifest.get('schema_version')}; need {SCHEMA_VERSION}."
        )
    if sha256_file(packed) != manifest["compressed"]["sha256"]:
        raise RWEError("SNAPSHOT_INVALID", "Compressed snapshot checksum differs from its manifest.")
    try:
        with gzip.open(packed, "rb") as source, target.open("wb") as unpacked:
            shutil.copyfileobj(source, unpacked)
        if sha256_file(target) != manifest["database"]["sha256"]:
            raise RWEError("SNAPSHOT_INVALID", "Snapshot database checksum differs from its manifest.")
        check_database(target)
    except (OSError, EOFError) as exc:
        target.unlink(missing_ok=True)
        raise RWEError("SNAPSHOT_INVALID", f"Snapshot could not be unpacked: {exc}") from exc
    except RWEError:
        target.unlink(missing_ok=True)
        raise
    return target
