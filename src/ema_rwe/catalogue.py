"""Catalogue side of the service: status, CSV imports, the medicines dictionary and protocol backfill."""

import asyncio
import re
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from .catalogue_update import app_update, current_snapshot
from .catalogue_update import status as update_status
from .domain import (
    RWEError,
    Study,
)
from .drugs import refresh_dictionary
from .ema import is_non_interventional
from .medicines import (
    EXPOSURE_RULES,
    find_medicines,
    known_class_names,
    known_medicine_names,
    pass_table_medicines,
)
from .pdf import (
    extract_pages,
)
from .selection import TYPED_SOURCES, source_categories
from .storage import import_csv
from .terminology import dictionary_paths
from .unmatched import unmatched_summary


def source_type_from_filename(filename: str) -> str | None:
    """<date>_<claims|ehr|registry>_export-data.csv -> the export's Data source type filter."""
    tokens = re.split(r"[_\-. ]+", filename.casefold())
    found = [t for t in TYPED_SOURCES if t in tokens]
    return found[0] if len(found) == 1 else None


class CatalogueMixin:
    """Service methods for the catalogue; uses settings, repo, client and archive of the Service."""

    @property
    def import_dir(self):
        return self.settings.import_dir or self.settings.db_path.parent / "imports"

    @property
    def study_import_dir(self):
        return self.import_dir / "studies"

    @property
    def source_type_import_dir(self):
        return self.import_dir / "source_type"

    async def refresh_drug_dictionary(self, force: bool = False):
        return await refresh_dictionary(force)

    def catalogue_status(self):
        snapshots = self.repo.imports()
        latest = snapshots[0] if snapshots else None
        now_ = datetime.now(UTC)
        # The catalogue is one full Studies export plus the Data-source-type-filtered exports that tag it: each
        # kind ages on its own, so a fresh registry export cannot make an old full export look current.
        exports: dict[str, dict] = {}
        for snapshot in snapshots:  # newest first
            kind = source_type_from_filename(snapshot["filename"] or "") or "studies"
            if kind not in exports:
                age_ = (now_ - datetime.fromisoformat(snapshot["imported_at"])).total_seconds()
                dated = re.match(r"(\d{8})_", snapshot["filename"] or "")
                exports[kind] = {
                    "filename": snapshot["filename"],
                    "imported_at": snapshot["imported_at"],
                    "export_date": dated[1] if dated else snapshot["imported_at"][:10].replace("-", ""),
                    "age_seconds": age_,
                    "status": "current" if age_ <= self.settings.catalogue_ttl else "stale",
                }
        if not snapshots:
            age, state = None, "not_imported"
        else:
            age = exports["studies"]["age_seconds"] if "studies" in exports else None
            state = (
                "current"
                if "studies" in exports and all(e["status"] == "current" for e in exports.values())
                else "stale"
            )
        # Exports of one refresh share a date (file name prefix, else import day); a typed export from another
        # month tags the studies of that month only.
        aligned = len({e["export_date"] for e in exports.values()}) <= 1
        return {
            "status": state,
            "exports": exports,
            "missing_source_type_exports": [t for t in ("claims", "ehr", "registry") if t not in exports],
            "snapshot_aligned": aligned,
            "studies_total": self.repo.study_count(),
            # The export date the catalogue was built from, and what the last look for a newer snapshot found.
            "catalogue_snapshot": current_snapshot(self.settings.db_path),
            "update": update_status(self.settings),
            "app_version": version("ema-rwe-mcp"),
            # Present only when a newer application release was seen; the server never replaces itself.
            **({"app_update_available": newer} if (newer := app_update(self.settings)) else {}),
            "latest_import": dict(
                latest, note="count is the row count of this one export, not the catalogue total"
            )
            if latest
            else None,
            "snapshot_count": len(snapshots),
            "age_seconds": age,
            "refresh_after_seconds": self.settings.catalogue_ttl,
            "import_directory": str(self.import_dir.resolve()),
            "study_import_directory": str(self.study_import_dir.resolve()),
            "source_type_import_directory": str(self.source_type_import_dir.resolve()),
            "source_type_imports": sorted(
                {t for s in snapshots if (t := source_type_from_filename(s["filename"] or ""))}
            ),
            "max_screening_studies": self.settings.max_screening_studies,
            "max_comparison_studies": self.settings.max_comparison_studies,
            "unmatched_terms": unmatched_summary(self.unmatched_log_path),
            "dictionaries": self._dictionary_status(),
            # Only an expired export or a missing full export calls for a browser export: AGENTS allows one
            # export, and exports of different dates are flagged (snapshot_note) rather than re-requested.
            "browser_refresh_recommended": state != "current",
            **(
                {}
                if aligned
                else {
                    "snapshot_note": "Exports have different dates: studies registered between those dates "
                    "are missing, or lack source-type tags, until the older export is refreshed."
                }
            ),
            "discovery_scope": "Only imported studies and individually retrieved Study IDs are searchable.",
            "browser_instruction": (
                "When refresh is needed (exports[*].status shows which are stale), use a visible "
                "user-initiated browser session to open the EMA Search "
                "page, select Studies/Non-interventional as appropriate, click Export Results once, save the "
                "CSV in study_import_directory, then call import_catalogue_csv. Wait on the same batch page; do not "
                "crawl result pages, start background synchronization, or create duplicate exports. "
                "Catalogue source types come only from exports filtered by Data source type and saved as "
                "<date>_<claims|ehr|registry>_export-data.csv in source_type_import_directory."
            ),
        }

    @staticmethod
    def _dictionary_status():
        """Configured dictionary files; a broken EMA_TERMINOLOGY_PATH is reported, not raised, so this
        diagnostic tool still answers."""
        try:
            return [p.name for p in dictionary_paths()]
        except RWEError as exc:
            return {"error": exc.as_dict()["error"]}

    def import_catalogue_csv(self, filename: str, column_map: dict[str, str] | None = None):
        if (
            not filename
            or len(filename) > 255
            or filename != Path(filename).name
            or Path(filename).suffix.casefold() != ".csv"
        ):
            raise RWEError(
                "INVALID_INPUT",
                "filename must be a CSV basename in the import directory (studies/, source_type/).",
            )
        root = self.import_dir.resolve()
        study_folder = self.study_import_dir.resolve()
        typed_folder = self.source_type_import_dir.resolve()
        candidates = [(f / filename).resolve() for f in (study_folder, typed_folder, root)]
        if any(path.parent not in {study_folder, typed_folder, root} for path in candidates):
            raise RWEError("INVALID_INPUT", "CSV path escaped the configured import directory.")
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise RWEError(
                "CSV_NOT_FOUND", "CSV was not found in the import directory (studies/, source_type/)."
            )
        # Tag by the file name in any folder, as catalogue_status reads it (and the CLI import-csv tags).
        source_type = source_type_from_filename(filename)
        if path.parent == typed_folder and not source_type:
            raise RWEError(
                "INVALID_INPUT",
                "Files in source_type/ must embed the export's Data source type: "
                "<date>_<claims|ehr|registry>_export-data.csv.",
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RWEError("CACHE_ERROR", "Could not inspect the CSV download.") from exc
        if not 0 < size <= 50 * 1024 * 1024:
            raise RWEError("CSV_SCHEMA_ERROR", "CSV must be non-empty and at most 50 MiB.")
        result = import_csv(self.repo, path, column_map, source_type)
        return {**result, "source_path": str(path), "catalogue": self.catalogue_status()}

    async def prefetch_protocols(
        self, source_type: str, interval: float = 30.0, limit: int | None = None, pause: float = 900.0
    ):
        """Fetch and keep the latest protocol of every study of one catalogue source type whose export lists
        one (a user-run command, never part of a search; spec v1.31).

        Resumable: studies with a kept protocol, or already found to have none, are skipped. On a rate limit
        the gap between studies widens to 60 seconds and the study is tried again; a second rate limit in a
        row pauses `pause` seconds first. After three pauses, the next rate limit stops the run.
        """
        if source_type not in TYPED_SOURCES or interval < 0 or (limit is not None and limit < 1):
            raise RWEError("INVALID_INPUT", f"source_type one of {TYPED_SOURCES}; interval >= 0; limit >= 1.")
        with self.repo.connection() as db:
            bodies = [r[0] for r in db.execute("SELECT body FROM studies").fetchall()]
        studies = [
            s
            for s in map(Study.model_validate_json, bodies)
            if is_non_interventional(s.study_type)
            and s.protocol_listed
            and source_type in source_categories(s.data_source_types)
        ]
        queue = [
            s
            for s in studies
            if not self.archive.list(s.study_id)
            and not self.repo.observation(s.study_id).get("prefetch_done")
        ]
        summary = {"studies": len(studies), "queued": len(queue), "fetched": 0, "no_protocol": 0}
        summary |= {"errors": {}, "rate_limited": 0, "pauses": 0, "stopped": None}
        gap, limited, pauses = interval, 0, 0
        pending = queue[:limit]
        while pending:
            study = pending[0]
            try:
                await self.get_protocol(study.study_id)
            except RWEError as exc:
                if exc.code == "EMA_RATE_LIMITED":
                    summary["rate_limited"] += 1
                    limited += 1
                    gap = max(gap, 60.0)
                    if limited >= 2:
                        if pauses >= 3:
                            summary["stopped"] = f"rate limited after 3 pauses at study {study.study_id}"
                            break
                        pauses += 1
                        summary["pauses"] += 1
                        await asyncio.sleep(pause)
                    await asyncio.sleep(gap)
                    continue
                if exc.code == "PROTOCOL_NOT_FOUND":
                    summary["no_protocol"] += 1
                    self.repo.observe(study.study_id, prefetch_done=True)
                else:  # outage or an unreadable document: recorded, and the next run tries it again
                    summary["errors"][exc.code] = summary["errors"].get(exc.code, 0) + 1
            else:
                summary["fetched"] += 1
            limited = pauses = 0
            pending.pop(0)
            if pending:
                await asyncio.sleep(gap)
        summary["gap_seconds"] = gap
        return summary

    async def backfill_protocols(
        self, interval: float = 60.0, limit: int | None = None, download: bool = True, reextract: bool = False
    ):
        """Fill the medicines of studies whose export lists none (a user-run command, never part of a search).

        1. No network: medicine names written in the title, description or objective.
        2. With download: for those whose export lists a protocol and that were not tried yet, the
           'Active substance' / 'Medicinal product' fields of the protocol's PASS information table.
        One study at a time, `interval` seconds apart on top of the HTTP interval; resumable (tried
        studies are skipped); stops at the first rate-limit response. Results are observations
        (protocol_observations), never catalogue values.
        """
        if interval < 0 or (limit is not None and limit < 1):
            raise RWEError("INVALID_INPUT", "interval must be >= 0 and limit >= 1.")
        labels = self.repo.atc_labels()
        names, classes = known_medicine_names(labels), known_class_names(labels)

        def rules(observed: dict, source: str) -> dict:
            return {**observed.get("exposure_rules", {}), source: EXPOSURE_RULES[source]}

        with self.repo.connection() as db:
            bodies = [r[0] for r in db.execute("SELECT body FROM studies").fetchall()]
        studies = [s for s in map(Study.model_validate_json, bodies) if is_non_interventional(s.study_type)]
        empty = [s for s in studies if not s.exposures]
        summary = {
            "studies_without_medicines": len(empty),
            "named_in_text": 0,
            "tried": 0,
            "from_protocol": 0,
            "no_protocol": 0,
            "errors": {},
            "stopped": None,
        }
        for study in studies:
            observed = self.repo.observation(study.study_id)
            # The catalogue's own medicines replace text matches once an export lists them.
            text = f"{study.title} {study.description} {study.objective}" if not study.exposures else ""
            in_text = [{"term": n, "source": "catalogue_text"} for n in find_medicines(text, names)]
            old = [e for e in observed.get("exposures", []) if e["source"] == "catalogue_text"]
            kept = [e for e in observed.get("exposures", []) if e["source"] != "catalogue_text"]
            if old != in_text or (
                in_text and observed.get("exposure_rules") != rules(observed, "catalogue_text")
            ):
                self.repo.observe(
                    study.study_id, exposures=kept + in_text, exposure_rules=rules(observed, "catalogue_text")
                )
            summary["named_in_text"] += bool(in_text)
        if reextract:  # read the PASS tables of protocols already kept again (after a parser fix); no network
            summary["reextracted"] = 0
            for study in empty:
                observed = self.repo.observation(study.study_id)
                if not observed.get("protocol_id"):
                    continue
                try:
                    pdf, _ = self.archive.load(observed["protocol_id"])
                    pages = await asyncio.to_thread(extract_pages, pdf)
                except RWEError:
                    continue
                kept = [e for e in observed.get("exposures", []) if e["source"] != "protocol_pass_table"]
                self.repo.observe(
                    study.study_id,
                    exposures=kept + pass_table_medicines(pages, names, classes, labels),
                    exposure_rules=rules(observed, "protocol_pass_table"),
                )
                summary["reextracted"] += 1
        if not download:
            return summary
        # A study is done once its protocol was read (or found absent, or failed for good); a run
        # interrupted midway (rate limit, outage, Ctrl-C) leaves it to the next run.
        queue = [
            s
            for s in empty
            if s.protocol_listed and not self.repo.observation(s.study_id).get("backfill_done")
        ]
        summary["queued"] = len(queue)
        for n, study in enumerate(queue[:limit]):
            if n:
                await asyncio.sleep(interval)
            summary["tried"] += 1
            try:
                result = await self.get_protocol(study.study_id)
                pdf, _ = self.archive.load(result["protocol"]["protocol_id"])
                pages = await asyncio.to_thread(extract_pages, pdf)
            except RWEError as exc:
                if exc.code in ("EMA_RATE_LIMITED", "EMA_UNAVAILABLE"):
                    summary["stopped"] = f"{exc.code} at study {study.study_id}; rerun later to resume"
                    break
                if exc.code == "PROTOCOL_NOT_FOUND":
                    summary["no_protocol"] += 1
                    self.repo.observe(study.study_id, backfill_done=True)
                else:  # this protocol cannot be read (not a PDF, encrypted, image-only, out of scope)
                    self.repo.observe(study.study_id, backfill_done=True, backfill_error=exc.code)
                    summary["errors"][exc.code] = summary["errors"].get(exc.code, 0) + 1
                continue
            found = pass_table_medicines(pages, names, classes, labels)
            observed = self.repo.observation(study.study_id)
            kept = [e for e in observed.get("exposures", []) if e["source"] != "protocol_pass_table"]
            self.repo.observe(
                study.study_id,
                exposures=kept + found,
                exposure_rules=rules(observed, "protocol_pass_table"),
                backfill_done=True,
            )
            summary["from_protocol"] += bool(found)
        return summary

    def import_all(self):
        """Rebuild tags from every export on disk: studies/ first, then source_type/ (tags accumulate in any order)."""
        folders = (self.study_import_dir, self.source_type_import_dir)
        files = [p for folder in folders if folder.is_dir() for p in sorted(folder.glob("*.csv"))]
        if not files:
            raise RWEError("CSV_NOT_FOUND", "No CSV found under studies/ or source_type/.")
        return [self.import_catalogue_csv(p.name) for p in files]
