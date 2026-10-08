r"""Build a monthly catalogue snapshot for publication as a GitHub release (data-YYYYMMDD).

    uv run scripts/build_catalogue_snapshot.py 20261001
    gh release create data-20261001 --target main --latest=false --title "Catalogue 2026-10-01" \
        --notes "Catalogue snapshot of the EMA exports dated 2026-10-01; see manifest.json" \
        dist/snapshots/20261001/ema-catalogue-20261001.sqlite3.gz dist/snapshots/20261001/manifest.json
    gh release download data-20261001 -D /tmp/check && uv run scripts/build_catalogue_snapshot.py --verify /tmp/check

Reads the four exports dated <id> under data/imports/{studies,source_type}/, imports them into a fresh
database, merges the protocol observations of the committed database, compacts and checks it, then writes
the database, its gzip and manifest.json to dist/snapshots/<id>/. --verify DIR checks a downloaded
snapshot (manifest.json and the gzip in DIR) against its manifest.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from ema_rwe.domain import RWEError
from ema_rwe.snapshot import build_snapshot, verify_snapshot

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "snapshot_id", nargs="?", help="Export date YYYYMMDD shared by the four CSV file names"
    )
    parser.add_argument("--verify", type=Path, help="Check a downloaded snapshot folder instead of building")
    parser.add_argument("--import-dir", type=Path, default=ROOT / "data" / "imports")
    parser.add_argument("--out", type=Path, help="Default: dist/snapshots/<snapshot_id>")
    parser.add_argument(
        "--observations-from",
        type=Path,
        default=ROOT / "data" / "ema.sqlite3",
        help="Database whose protocol observations (medicine backfill) the snapshot keeps; 'none' to skip",
    )
    args = parser.parse_args()
    if args.verify:
        return verify(args.verify)
    if not re.fullmatch(r"\d{8}", args.snapshot_id or ""):
        parser.error("snapshot_id must be YYYYMMDD")
    observations = None if str(args.observations_from) == "none" else args.observations_from
    try:
        manifest = build_snapshot(
            args.import_dir,
            args.snapshot_id,
            args.out or ROOT / "dist" / "snapshots" / args.snapshot_id,
            observations,
        )
    except RWEError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def verify(folder: Path) -> int:
    """Check folder/manifest.json and the gzip it names; the unpacked database is discarded."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as work:
            verify_snapshot(manifest, folder / manifest["compressed"]["name"], Path(work) / "check.sqlite3")
        summary = {"verified": manifest["snapshot_id"], "studies_total": manifest["studies_total"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        exc = RWEError("SNAPSHOT_INVALID", f"Cannot read the snapshot in {folder}: {exc}")
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 1
    except RWEError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
