r"""Build a monthly catalogue snapshot for publication as a GitHub release (data-YYYYMMDD).

    uv run scripts/build_catalogue_snapshot.py 20261001
    gh release create data-20261001 --target main --latest=false --title "Catalogue 2026-10-01" \
        --notes "Catalogue snapshot of the EMA exports dated 2026-10-01; see manifest.json" \
        dist/snapshots/20261001/ema-catalogue-20261001.sqlite3.gz dist/snapshots/20261001/manifest.json
    gh release download data-20261001 -D /tmp/check && uv run scripts/build_catalogue_snapshot.py --verify /tmp/check

Reads the four exports dated <id> under data/imports/{studies,source_type}/, imports them into a fresh
database, merges the protocol observations of the committed database, compacts and checks it, then writes
the database, its gzip and manifest.json to dist/snapshots/<id>/. It then runs the extraction reference
against the new catalogue (scripts/extraction_eval.py: the reference studies are still listed as
non-interventional studies, and every reference criterion is still found in their protocols) and exits 1,
leaving the files, when that fails: do not publish before it passes (--skip-gold to leave it out). --verify DIR checks a downloaded
snapshot (manifest.json and the gzip in DIR) against its manifest; with --unpack-to FILE the verified
database is kept there (for an application release: --unpack-to data/ema.sqlite3 makes the bundled
catalogue the published snapshot itself).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
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
    parser.add_argument(
        "--unpack-to", type=Path, help="With --verify: keep the verified database at this path (replaces it)"
    )
    parser.add_argument("--import-dir", type=Path, default=ROOT / "data" / "imports")
    parser.add_argument("--out", type=Path, help="Default: dist/snapshots/<snapshot_id>")
    parser.add_argument(
        "--observations-from",
        type=Path,
        default=ROOT / "data" / "ema.sqlite3",
        help="Database whose protocol observations (medicine backfill) the snapshot keeps; 'none' to skip",
    )
    parser.add_argument(
        "--facts-from",
        type=Path,
        help="Database whose distributed facts (ema-rwe export-facts) the snapshot carries; default none",
    )
    parser.add_argument(
        "--skip-gold", action="store_true", help="Do not run the extraction reference on the new catalogue"
    )
    args = parser.parse_args()
    if args.unpack_to and not args.verify:
        parser.error("--unpack-to needs --verify")
    if args.verify:
        return verify(args.verify, args.unpack_to)
    if not re.fullmatch(r"\d{8}", args.snapshot_id or ""):
        parser.error("snapshot_id must be YYYYMMDD")
    observations = None if str(args.observations_from) == "none" else args.observations_from
    try:
        manifest = build_snapshot(
            args.import_dir,
            args.snapshot_id,
            args.out or ROOT / "dist" / "snapshots" / args.snapshot_id,
            observations,
            args.facts_from,
        )
    except RWEError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if args.skip_gold:
        return 0
    out = args.out or ROOT / "dist" / "snapshots" / args.snapshot_id
    return gold(out / manifest["database"]["name"])


def gold(database: Path) -> int:
    """Run the extraction reference with the new catalogue: on a copy, so the published file stays as hashed."""
    archive = os.getenv("EMA_PROTOCOL_DIR") or str(ROOT / "data" / "protocols")
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / "ema.sqlite3"
        shutil.copyfile(database, copy)
        env = {**os.environ, "EMA_DB_PATH": str(copy), "EMA_PROTOCOL_DIR": archive}
        for command in (["catalogue"], ["check", "--require"]):
            print(f"== extraction reference: {' '.join(command)}", file=sys.stderr, flush=True)
            done = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "extraction_eval.py"), *command],
                env=env,
                stdout=sys.stderr,  # stdout carries the manifest only
                check=False,
            )
            if done.returncode:
                print(
                    "The extraction reference fails with this catalogue: do not publish the snapshot. "
                    "See tests/fixtures/extraction_gold/README.md.",
                    file=sys.stderr,
                )
                return 1
    return 0


def verify(folder: Path, unpack_to: Path | None = None) -> int:
    """Check folder/manifest.json and the gzip it names. The unpacked database is discarded, or moved to
    unpack_to once it has passed every check (never a half-checked file)."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=unpack_to.parent if unpack_to else None) as work:
            checked = verify_snapshot(
                manifest, folder / manifest["compressed"]["name"], Path(work) / "check.sqlite3"
            )
            if unpack_to:
                for leftover in (unpack_to.with_name(unpack_to.name + suffix) for suffix in ("-wal", "-shm")):
                    leftover.unlink(missing_ok=True)  # of the database being replaced
                checked.replace(unpack_to)
        summary = {"verified": manifest["snapshot_id"], "studies_total": manifest["studies_total"]}
        if unpack_to:
            summary["unpacked_to"] = str(unpack_to)
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
