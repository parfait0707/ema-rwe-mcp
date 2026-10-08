import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .archive import ProtocolArchive
from .comparison import Comparisons
from .config import Settings
from .domain import (
    AnalogousTerm,
    CodeCandidate,
    Extraction,
    ProtocolAnswer,
    RWEError,
    SourcePreference,
    Study,
    now,
    with_notes,
)
from .drugs import refresh_dictionary
from .ema import BASE, is_non_interventional, parse_documents, parse_study, select_protocol
from .exploration import Explorer
from .http import EMAClient
from .llm import (
    EXTRACTION_PROMPT,
    complete_json,
    configured,
    extract_with_provider,
    merge_extractions,
    translate_headings,
)
from .medicines import (
    EXPOSURE_RULES,
    GENERIC,
    MAX_NAMES,
    expand_medicine,
    find_medicines,
    known_class_names,
    known_medicine_names,
    pass_table_medicines,
)
from .pdf import (
    PARSER_VERSION,
    audit_extraction,
    clean_translations,
    english,
    extract_pages,
    finalize_extraction,
    heading_texts,
    reading_hash,
    reading_order,
    sections,
    stored_translations,
    text_layer,
    validate_evidence,
)
from .ranking import ScreeningBlock, order_key, screen
from .selection import TYPED_SOURCES, SearchFilters, compact, selection
from .storage import Repository, import_csv
from .terminology import dictionary_paths, labelled_phrases, proposed_codes
from .vocabulary import expand

SCHEMA_VERSION = Extraction.model_fields["schema_version"].default

# ICD-10-based guidance for generating disease synonyms and analogous concepts. The MCP client (or the
# optional server LLM) follows it; the server ships no disease dictionary and no ICD-10 content.
ICD10_SYNONYM_GUIDANCE = (
    "Explore disease synonyms based on ICD-10 (WHO 2019): identify the 3-character category and the "
    "4-character subcategories the concept corresponds to, and use their titles, inclusion terms and common "
    "clinical names as English search terms, with the codes as ICD-10 code candidates (unverified, from your "
    "own knowledge). Never use Excludes terms as synonyms. Other categories in the same ICD-10 block are "
    "different diseases: give them as analogous_terms with relation=sibling; the block or chapter grouping is "
    "relation=broader; complications or related conditions are relation=associated. National modifications "
    "(ICD-10-CM, ICD-10-GM, Japanese adaptations) may differ from WHO ICD-10."
)
# Study fields the detail-page parser does not (fully) read; a page refresh keeps the stored values.
CATALOGUE_FIELDS = (
    "exposures",
    "conditions",
    "outcomes",
    "objective",
    "catalogue_data_sources",
    "protocol_listed",
)
CATEGORY_GUIDANCE = (
    "Catalogue records often name only a category, so give each concept category_terms too: the ICD-10 "
    "block or chapter title, and the composite or umbrella outcomes studies of that concept use (for example "
    "MACE or adverse events of special interest; these are illustrations, not a list to choose from); the "
    "server adds the class of a "
    "medicine itself. Keep them "
    "specific to the concept: generic terms such as 'adverse drug reactions' only add noise. Category matches "
    "are ranked below specific ones. Codes rarely appear in catalogue records; they matter in the protocol PDFs."
)
CLIENT_EXPANSION_INSTRUCTION = (
    "Translate the question yourself and pass the result to compare_protocols as blocks, one per concept "
    "({role: outcome|condition|exposure|any, queries: 1-20 short English names and paraphrases, "
    "category_terms}); blocks are AND-ed, terms within a block OR-ed, and every column is searched with the "
    "role used for ranking. A single-concept question may instead use queries, role and category_terms. "
    + ICD10_SYNONYM_GUIDANCE
    + " "
    + CATEGORY_GUIDANCE
    + " For medicines give the English INN and the EU and US product names (each query is matched as one whole "
    "name), plus abbreviations (e.g. TNFi, JAKi, DOACs), noun variants (drugs/medicines/medications) and spellings "
    "with and without hyphens; for an absent medicine also give its 5th-level ATC code. For a requested medicine class, put the class name "
    "and its ATC 3rd/4th-level code (e.g. N03A) in queries; the server adds the member medicines that the "
    "catalogue and the EMA medicines dictionary code under it (medicine_expansion). Keep outcome, exposure "
    "and comorbidity roles apart."
)


def read_log(path: Path) -> dict:
    """The unmatched-query log; a missing or unreadable file counts as empty."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def record_unmatched(path: Path, text: str, tool: str, expansion: dict) -> bool:
    """Log a non-English query that neither the concept dictionary nor the drug dictionary recognised.

    The log lives beside the database (not inside it, which is committed) and is the evidence for
    deciding which concepts to add to a user dictionary (data/dictionaries/). Nothing is logged while
    no dictionary is configured. English-only queries are not logged:
    the index is English, so they need no translation.
    """
    text = " ".join(text.split())[:200]
    clinical = expansion["clinical"]
    if (
        not clinical["dictionaries"]  # without a dictionary every Japanese query is unmatched by design
        or not text
        or text.isascii()
        or expansion["concepts"]
        or clinical["concepts"]
        or clinical["drugs"]["total_matches"]
    ):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    log = read_log(path)
    entry = log.setdefault(text, {"count": 0, "first_seen": now(), "tools": []})
    entry["count"] += 1
    entry["last_seen"] = now()
    if tool not in entry["tools"]:
        entry["tools"].append(tool)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True


def unmatched_summary(path: Path, limit: int = 20) -> dict:
    """Most frequent unrecognised queries, for deciding whether the dictionary needs new concepts."""
    log = read_log(path)
    ranked = sorted(log.items(), key=lambda kv: (-kv[1]["count"], kv[1].get("last_seen", "")))
    return {
        "path": str(path),
        "distinct": len(log),
        "top": [{"text": text, **entry} for text, entry in ranked[:limit]],
        "note": "Japanese queries that matched no concept or medicine of the configured dictionaries "
        "(recorded only while a dictionary is configured). Add frequent ones to a dictionary under "
        "data/dictionaries/.",
    }


def expansion_summary(expansion: dict) -> dict:
    """The parts of a query expansion a caller acts on; plan_study_search returns the full form."""
    clinical = expansion["clinical"]
    return {
        "synonyms": expansion["synonyms"],
        "english_terms": clinical["english_terms"],
        "related_terms": clinical["related_terms"],
        "analogous_terms": [f"{a['term']} ({a['relation']})" for a in clinical["analogous_terms"]],
        "codes": [f"{c['system']}:{c['code']}" for c in clinical["code_candidates"]],
        "drug_terms": clinical["drugs"]["english_terms"],
        "drugs_need_refresh": clinical["drugs"]["needs_refresh"],
    }


def analogous_terms(expansions: list[dict], extra: list[AnalogousTerm] | None) -> list[dict]:
    """Dictionary and caller analogous concepts with their relation, one entry per term."""
    terms = [a for e in expansions for a in e["clinical"]["analogous_terms"]]
    terms += [{**a.model_dump(), "concept_id": None} for a in extra or []]
    unique = {}
    for term in terms:
        unique.setdefault(term["term"].casefold(), term)
    return list(unique.values())


def source_type_from_filename(filename: str) -> str | None:
    """<date>_<claims|ehr|registry>_export-data.csv -> the export's Data source type filter."""
    tokens = re.split(r"[_\-. ]+", filename.casefold())
    found = [t for t in TYPED_SOURCES if t in tokens]
    return found[0] if len(found) == 1 else None


class Service:
    def __init__(self, settings: Settings | None = None, client=None, repo=None):
        self.settings = settings or Settings()
        self.repo = repo or Repository(self.settings.db_path)
        self.client = client or EMAClient(self.settings)
        self.archive = ProtocolArchive(
            self.settings.protocol_dir or self.settings.db_path.parent / "protocols"
        )
        self.explorer = Explorer(self.settings, self.archive, self.repo)
        self.comparisons = Comparisons(self)
        # ponytail: in-process registry of running provider extractions; a server restart loses them
        # and the next analyze_protocol simply starts over (add DB checkpoints if that ever matters).
        self._extractions: dict[tuple[str, str], tuple[asyncio.Task, dict]] = {}  # (study, fingerprint)
        self._partials: dict[tuple[str, str], dict[int, Extraction]] = {}

    @property
    def import_dir(self):
        return self.settings.import_dir or self.settings.db_path.parent / "imports"

    @property
    def study_import_dir(self):
        return self.import_dir / "studies"

    @property
    def source_type_import_dir(self):
        return self.import_dir / "source_type"

    @property
    def unmatched_log_path(self):
        return self.settings.unmatched_log_path or self.settings.db_path.parent / "terminology_unmatched.json"

    async def close(self):
        await self.client.close()

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
            "browser_refresh_recommended": state != "current" or not aligned,
            "discovery_scope": "Only imported studies and individually retrieved Study IDs are searchable.",
            "browser_instruction": (
                "When refresh is needed, use a visible user-initiated browser session to open the EMA Search "
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
                "INVALID_INPUT", "filename must be a CSV basename inside the Studies import directory."
            )
        root = self.import_dir.resolve()
        study_folder = self.study_import_dir.resolve()
        typed_folder = self.source_type_import_dir.resolve()
        candidates = [(f / filename).resolve() for f in (study_folder, typed_folder, root)]
        if any(path.parent not in {study_folder, typed_folder, root} for path in candidates):
            raise RWEError("INVALID_INPUT", "CSV path escaped the configured import directory.")
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise RWEError("CSV_NOT_FOUND", "CSV was not found in the Studies import directory.")
        source_type = source_type_from_filename(filename) if path.parent == typed_folder else None
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
        """Rebuild tags from every export on disk: full Studies export(s) first, then the typed exports."""
        folders = (self.study_import_dir, self.source_type_import_dir)
        files = [p for folder in folders if folder.is_dir() for p in sorted(folder.glob("*.csv"))]
        if not files:
            raise RWEError("CSV_NOT_FOUND", "No CSV found under studies/ or source_type/.")
        return [self.import_catalogue_csv(p.name) for p in files]

    def search_studies(
        self,
        query: str,
        limit: int = 5,
        darwin_only: bool = False,
        status: list[str] | None = None,
        analyzed_only: bool = False,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
        filters: SearchFilters | None = None,
        role: str = "any",
        detail: str = "compact",
        match_scope: str = "concept",
        analogous: list[AnalogousTerm] | None = None,
    ):
        if not 1 <= limit <= 20:
            raise RWEError("INVALID_INPUT", "limit must be 1..20; preview also obeys max_comparison_studies.")
        if detail not in ("compact", "full"):
            raise RWEError("INVALID_INPUT", "detail must be compact or full.")
        candidates = self.repo.search(
            query,
            None,
            darwin_only,
            status,
            analyzed_only,
            synonyms,
            codes,
            filters,
            role,
            match_scope,
            analogous,
        )
        results = candidates[: min(limit, self.settings.max_comparison_studies)]
        for result in results:
            result["protocol_id"] = (result.get("protocol_source") or {}).get("protocol_id")
            result["local_protocols"] = [
                {k: r.get(k) for k in ("protocol_id", "local_filename", "version", "document_url")}
                for r in self.archive.list(result["study_id"])
            ]
        if detail == "compact":
            results = [
                {
                    **compact(r),
                    **{k: r[k] for k in ("protocol_id", "local_protocols", "protocol_data_sources")},
                }
                for r in results
            ]
        expansion = expand(query, synonyms, codes)
        record_unmatched(self.unmatched_log_path, query, "search_studies", expansion)
        return {
            "query": query,
            "role": role,
            **self._selection(candidates),
            **self._match_scope(
                [query],
                [expansion],
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                match_scope,
                analogous,
                bool(candidates),
                status,
                analyzed_only,
            ),
            "max_comparison_studies": self.settings.max_comparison_studies,
            "returned_count": len(results),
            "results_are_preview": len(results) < len(candidates),
            "detail": detail,
            "filters": (filters or SearchFilters()).model_dump(),
            "results": results,
            "query_expansion": expansion if detail == "full" else expansion_summary(expansion),
            "search_mode": "local_fts_near_bm25",
            "network_requests": 0,
            **self._catalogue_summary(bool(candidates)),
            "note": "Protocol fields are not_analyzed until analyze_protocol and extraction are completed. "
            "detail=full returns descriptions, provenance and the full query expansion.",
        }

    def _union(
        self,
        queries,
        darwin_only,
        synonyms,
        codes,
        filters,
        role,
        scope="concept",
        analogous=None,
        status=None,
        analyzed_only=False,
        split_long=True,
    ):
        """Deduplicated candidates of all query variants; matched_terms accumulate across variants.
        An analogous search excludes the concept matches of every variant, not just its own."""
        exclude = (
            self._concept_ids(queries, synonyms, codes, role, split_long) if scope == "analogous" else None
        )
        unique = {}
        for query in queries:
            for row in self.repo.search(
                query,
                None,
                darwin_only,
                status,
                analyzed_only,
                synonyms,
                codes,
                filters,
                role,
                scope,
                analogous,
                exclude,
                split_long,
            ):
                kept = unique.setdefault(row["study_id"], row)
                kept["matched_terms"] = list(dict.fromkeys(kept["matched_terms"] + row["matched_terms"]))
                kept["matched_term_sources"] = {**row["matched_term_sources"], **kept["matched_term_sources"]}
        return unique

    def _match_scope(
        self,
        queries,
        expansions,
        darwin_only,
        synonyms,
        codes,
        filters,
        role,
        scope,
        analogous,
        has_candidates,
        status=None,
        analyzed_only=False,
        split_long=True,
        derived=frozenset(),
    ):
        """How candidates matched, plus the analogous-concept fallback when the concept itself has no study."""
        terms = analogous_terms(expansions, analogous)
        result = {"match_scope": scope}
        if scope == "analogous":
            result["analogous_terms"] = terms
            result["analogous_note"] = (
                "Every candidate matched only an analogous concept, not the requested one; present its "
                "definitions as those of the analogous concept."
                if has_candidates
                else "No study matched these analogous concepts; relax filters or role, or propose other "
                "analogous_terms."
            )
        elif not has_candidates:
            union_args = (
                queries,
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                "analogous",
                analogous,
                status,
                analyzed_only,
                split_long,
            )
            result["analogous_fallback"] = self._fallback(
                queries, terms, synonyms, codes, role, union_args, split_long, derived
            )
        return result

    def _concept_ids(self, queries, synonyms, codes, role, split_long=True) -> set[str]:
        return set().union(*(self.repo.concept_ids(q, synonyms, codes, role, split_long) for q in queries))

    def _analogous_screen(
        self,
        queries,
        terms,
        darwin_only,
        synonyms,
        codes,
        filters,
        role,
        status=None,
        analyzed_only=False,
        split_long=True,
        derived=frozenset(),
    ) -> dict[str, dict]:
        """Analogous-concept candidates, screened and ranked like concept ones: the analogous terms form
        one block, each searched as one phrase; studies matching the requested concept are excluded.
        Each matched term reports its origin: catalogue_atc (built by the server from an ATC code),
        caller or dictionary."""
        terms = [t for t in terms if re.search(r"\w", t["term"])]  # a blank term would match every study
        if not terms:
            return {}
        # Studies that mention the requested concept anywhere are its own, not analogous candidates.
        exclude = self._concept_ids(queries, synonyms, codes, "any", split_long)

        def search(term: str, column_role: str, with_expansion: bool) -> list[dict]:
            rows = self.repo.search(
                term,
                None,
                darwin_only,
                status,
                analyzed_only,
                None,
                None,
                filters,
                column_role,
                split_long=False,
                exclude=exclude,
            )
            # The index labels a match by the term's content words ('drugs excl insulins'); report the
            # analogous term as written, so its origin and study_count line up.
            phrases = labelled_phrases(term, expand(term, whole_term=True), False)
            own = phrases[0][0] if phrases else term
            for row in rows:
                row["matched_terms"] = [term if t == own else t for t in row["matched_terms"]]
                row["matched_term_sources"] = {
                    term if t == own else t: v for t, v in row["matched_term_sources"].items()
                }
            return rows

        origin = {
            t["term"]: "catalogue_atc"
            if t["term"] in derived
            else "caller"
            if t.get("concept_id") is None
            else "dictionary"
            for t in terms
        }
        block = ScreeningBlock.model_construct(role=role, queries=list(origin), category_terms=[])
        rows = screen(search, [block])
        for row in rows:
            row["match_basis"] = "analogous"
            row["matched_term_sources"] = {
                t: origin.get(t, source) for t, source in row["matched_term_sources"].items()
            }
        return {row["study_id"]: row for row in rows}

    def _fallback(
        self, queries, terms, synonyms, codes, role, union_args, split_long=True, derived=frozenset()
    ):
        # Concept studies in the index that filters, darwin_only, status or analyzed_only removed:
        # then the concept is not absent, and the caller must say so before offering analogues.
        filtered_out = len(self._concept_ids(queries, synonyms, codes, role, split_long))
        darwin_only, filters, status, analyzed_only = (
            union_args[1],
            union_args[4],
            union_args[8],
            union_args[9],
        )
        rows = list(
            self._analogous_screen(
                queries,
                terms,
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                status,
                analyzed_only,
                split_long,
                derived,
            ).values()
        )
        selected = self._selection(rows)
        counts = {
            t["term"]: sum(t["term"].casefold() in {m.casefold() for m in r["matched_terms"]} for r in rows)
            for t in terms
        }
        if filtered_out:
            state = "concept_filtered_out"
            instruction = (
                f"{filtered_out} indexed studies match the requested concept but were removed by filters, "
                "darwin_only, status or analyzed_only. Tell the user and offer to relax those first; offer the "
                "analogous concepts below only as an alternative."
            )
        elif not terms:
            state = "no_analogous_terms"
            instruction = (
                "No study matched the requested concept and no analogous concept is known. Propose clinically "
                "analogous English concepts (broader category, sibling disease, associated condition) and rerun "
                "with match_scope='analogous' and analogous_terms. For a medicine, pass its 5th-level ATC code "
                "in queries (the server then offers its class and the class members the catalogue records) or "
                "propose medicines of the same pharmacological class. " + ICD10_SYNONYM_GUIDANCE
            )
        elif rows:
            state = "available"
            instruction = (
                "No study matched the requested concept itself. Tell the user so, show these analogous concepts "
                "with their relation and study_count, and on agreement rerun compare_protocols with "
                "match_scope='analogous' (same queries and filters, passing a block's queries and role as "
                "queries/role; phrase the question for the analogous concept). Label every result as an analogous concept, never as the requested one."
            )
        else:
            state = "no_matches"
            instruction = (
                "Neither the requested concept nor its known analogous concepts matched. Tell the user so; "
                "relax filters or role, or propose further analogous concepts via analogous_terms."
            )
        return {
            "status": state,
            "concept_index_matches_before_filters": filtered_out,
            "analogous_terms": [{**t, "study_count": counts[t["term"]]} for t in terms],
            **{k: selected[k] for k in ("total_matches", "candidates", "candidates_listed")},
            "instruction": instruction,
        }

    def _selection(self, candidates):
        return selection(candidates, self.settings.max_screening_studies, self.settings.max_listed_candidates)

    def _catalogue_summary(self, has_candidates: bool):
        """Only the catalogue facts a search caller acts on; catalogue_status has the rest."""
        catalogue = self.catalogue_status()
        return {
            "catalogue": {
                k: catalogue[k] for k in ("status", "source_type_imports", "browser_refresh_recommended")
            },
            "catalogue_action": (
                "none"
                if has_candidates
                else "browser_export_then_import"
                if catalogue["browser_refresh_recommended"]
                else "refine_queries_or_request_fresh_export"
            ),
        }

    async def compare_protocols(
        self,
        question: str,
        queries: list[str],
        filters: SearchFilters | None = None,
        darwin_only: bool = False,
        synonyms=None,
        codes=None,
        source_preference: SourcePreference | None = None,
        role: str = "any",
        study_ids: list[str] | None = None,
        match_scope: str = "concept",
        analogous: list[AnalogousTerm] | None = None,
        category_terms: list[str] | None = None,
        blocks: list[ScreeningBlock] | None = None,
        check_protocols: int = 0,
    ):
        given_blocks = bool(blocks)
        if blocks:
            if queries or synonyms or codes or category_terms or role != "any" or match_scope != "concept":
                raise RWEError(
                    "INVALID_INPUT",
                    "With blocks, put every term and role in its block (role, queries, category_terms); queries, "
                    "role, synonyms, codes, category_terms and match_scope=analogous are not combined with blocks.",
                )
            queries = [q for b in blocks for q in b.queries]
        if (
            not question.strip()
            or len(question) > 2000
            or not 1 <= len(queries) <= (40 if given_blocks else 20)
            or any(not q.strip() for q in queries)
            or not 0 <= check_protocols <= 20
        ):
            raise RWEError(
                "INVALID_INPUT",
                "A question and 1..20 nonempty search queries (1..40 across blocks) are required; "
                "check_protocols is 0..20.",
            )
        # filters.data_source_types narrows catalogue candidates; only source_preference ranks PDF evidence.
        preference = source_preference
        if not blocks:
            try:
                blocks = [ScreeningBlock(role=role, queries=queries, category_terms=category_terms or [])]
            except ValidationError as exc:
                raise RWEError(
                    "INVALID_INPUT", "Invalid role or category_terms (1..20 nonempty terms)."
                ) from exc
        # The single block's role drives the analogous fallback, whichever form the caller used.
        role = blocks[0].role if len(blocks) == 1 else role
        medicines: list[dict] = []
        derived: set[str] = set()  # analogous terms the server built from ATC codes
        if match_scope == "analogous":
            # The ATC class offered on a zero-hit result is searched on rerun, as the fallback said.
            atc_terms = self._atc_analogues(queries)
            derived |= {t.term for t in atc_terms}
            analogous = [*(analogous or []), *atc_terms]
            terms = analogous_terms([expand(q, synonyms, codes, whole_term=True) for q in queries], analogous)
            unique = self._analogous_screen(
                queries, terms, darwin_only, synonyms, codes, filters, role, split_long=False, derived=derived
            )
        else:

            def search(term: str, column_role: str, with_expansion: bool) -> list[dict]:
                return self.repo.search(
                    term,
                    None,
                    darwin_only,
                    None,
                    False,
                    synonyms if with_expansion else None,
                    codes if with_expansion else None,
                    filters,
                    column_role,
                    split_long=False,
                )

            # Every column, ranked: the role is a ranking signal, not a filter (empty Outcomes fields).
            sources: dict[str, str] = {}
            unique = {
                row["study_id"]: row
                for row in screen(search, self._with_medicines(blocks, medicines, sources))
            }
            if not unique and len(blocks) == 1:
                # An absent medicine: offer the catalogue's members of the class its ATC code belongs to.
                atc_terms = self._atc_analogues(blocks[0].queries)
                derived |= {t.term for t in atc_terms}
                analogous = [*(analogous or []), *atc_terms]
            for row in unique.values():
                # Server-added medicine names say where they came from, not that the caller typed them.
                for term, source in row["matched_term_sources"].items():
                    if source == "query" and term in sources:
                        row["matched_term_sources"][term] = sources[term]
        candidates = list(unique.values())
        checked = 0
        listed = self.settings.max_screening_studies < len(candidates) <= self.settings.max_listed_candidates
        requests = 0
        if check_protocols and listed and study_ids is None and match_scope == "concept":
            before = getattr(self.client, "requests_made", 0)
            checked = await self._check_protocols(candidates[:check_protocols])
            requests = getattr(self.client, "requests_made", 0) - before
            candidates.sort(key=order_key)
        # Reported as matched: each variant is one whole name, as in the search itself.
        expansions = [expand(q, synonyms, codes, whole_term=True) for q in queries]
        for query, expansion in zip(queries, expansions):
            record_unmatched(self.unmatched_log_path, query, "compare_protocols", expansion)
        if study_ids is not None:
            # An explicit user choice among the listed candidates; never a silent top N.
            if (
                not study_ids
                or len(study_ids) != len(set(study_ids))
                or len(study_ids) > self.settings.max_screening_studies
                or not set(study_ids) <= set(unique)
            ):
                raise RWEError(
                    "INVALID_INPUT",
                    "study_ids must be unique candidate Study IDs from this search, within max_screening_studies.",
                )
            candidates = [unique[sid] for sid in study_ids]
        search = {
            **self._selection(candidates),
            "max_comparison_studies": self.settings.max_comparison_studies,
            "source_preference": preference.model_dump() if preference else None,
            "queries": queries,
            "role": role,
            "blocks": [b.model_dump() for b in blocks],
            "medicine_expansion": medicines,
            "ranking": "Candidates are ordered, never cut: specific-term matches before category-only matches, "
            "matches in the role's own columns first, studies whose export lists a protocol before unlisted ones, "
            "secondary-use data before surveys, then fused BM25 rank; studies with an image-only protocol go last"
            + ("; studies checked to have no published protocol go last." if checked else "."),
            "protocols_checked": checked,
            "selected_study_ids": study_ids,
            "filters": (filters or SearchFilters()).model_dump(),
            "query_expansions": [expansion_summary(e) for e in expansions],
            **self._match_scope(
                queries,
                expansions,
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                match_scope,
                analogous,
                bool(unique) or len(blocks) > 1,
                split_long=False,
                derived=frozenset(derived),
            ),
            "darwin_only": darwin_only,
            "search_scope": "local catalogue metadata and saved analysis only",
            **self._catalogue_summary(bool(candidates)),
        }
        if len(blocks) > 1 and not unique:
            search["analogous_fallback"] = {
                "status": "not_available_for_blocks",
                "instruction": "No study matched every block. Report each block separately (run compare_protocols "
                "per block) or broaden a block with category_terms; analogous concepts apply to one block at a time.",
            }
        if not 1 <= len(candidates) <= self.settings.max_screening_studies:
            return {**search, "network_requests": requests, "rows": [], "pdf_downloads": 0}
        return await self.comparisons.prepare(question, candidates, search)

    def _with_medicines(
        self, blocks: list[ScreeningBlock], report: list[dict], sources: dict[str, str]
    ) -> list[ScreeningBlock]:
        """Blocks with the names of each medicine or medicine class added (see medicines.expand_medicine).

        At most MAX_NAMES specific names are added per question, the class names first. `report` records
        what was added per query; `sources` maps each added name the caller typed in no block to its origin.
        """
        labels = self.repo.atc_labels()
        typed = {t.casefold() for b in blocks for t in [*b.queries, *b.category_terms]}
        budget = MAX_NAMES
        out = []
        for index, block in enumerate(blocks):
            known = {t.casefold() for t in [*block.queries, *block.category_terms]}
            queries, category = list(block.queries), list(block.category_terms)
            for query in block.queries:
                found = expand_medicine(query, labels)
                if not found:
                    continue
                names = [t for t in found["queries"] if t.casefold() not in known]
                added = {"queries": names[:budget], "omitted": len(names[budget:])}
                budget -= len(added["queries"])
                added["category_terms"] = [t for t in found["category_terms"] if t.casefold() not in known]
                known |= {t.casefold() for t in [*added["queries"], *added["category_terms"]]}
                queries += added["queries"]
                category += added["category_terms"]
                sources |= {t: found["queries"][t] for t in added["queries"] if t.casefold() not in typed}
                report.append({"block": index, **found, **added})
            out.append(block.model_copy(update={"queries": queries, "category_terms": category}))
        return out

    def _atc_analogues(self, queries: list[str]) -> list[AnalogousTerm]:
        """Analogous concepts for a medicine with no study: its 4th-level ATC class (broader) and the class
        members the catalogue records (sibling), from the 5th-level codes the caller typed. Names only."""
        labels = self.repo.atc_labels()
        terms = []
        for query in queries:
            code = query.strip().upper()
            if not re.fullmatch(r"[A-Z]\d{2}[A-Z]{2}\d{2}", code):
                continue
            parent = code[:5]
            members = [
                labels[c] for c in sorted(labels) if len(c) == 7 and c.startswith(parent) and c != code
            ]
            broader = labels.get(parent) or labels.get(code[:4])  # the 3rd level when the 4th has no name
            for name, relation in [(broader, "broader"), *((m, "sibling") for m in members)]:
                if name and name.isascii() and len(name) <= 150 and not GENERIC.fullmatch(name):
                    terms.append(AnalogousTerm(term=name, relation=relation))
        return terms

    async def _check_protocols(self, rows: list[dict]) -> int:
        """Mark whether each top candidate's Study documents list a protocol (no PDF download). A study
        without one cannot answer a definition question; it is ranked last, never removed."""
        for row in rows:
            try:
                await self.get_protocol(row["study_id"], download=False)
                row["protocol_available"] = row["protocol_found"] = True
            except RWEError as exc:
                # No protocol, or no longer a non-interventional study: neither can answer, so rank last.
                unusable = exc.code in ("PROTOCOL_NOT_FOUND", "STUDY_OUT_OF_SCOPE")
                row["protocol_available"] = False if unusable else None
                if exc.code == "PROTOCOL_NOT_FOUND":
                    row["protocol_found"] = False
                if exc.code == "STUDY_OUT_OF_SCOPE":
                    row["out_of_scope"] = True
        return len(rows)

    async def get_protocol_comparison(
        self, comparison_id: str, selected_study_ids: list[str] | None = None, detail: str = "compact"
    ):
        return await asyncio.to_thread(self.comparisons.collect, comparison_id, selected_study_ids, detail)

    async def get_study(self, study_id: str, refresh: bool = False):
        if not re.fullmatch(r"\d{1,20}", study_id):
            raise RWEError("INVALID_INPUT", "study_id must be the numeric Study ID (not the Drupal node ID).")
        study = self.repo.get(study_id)
        if study and study.detail_checked_at and not refresh:
            age = (datetime.now(UTC) - datetime.fromisoformat(study.detail_checked_at)).total_seconds()
            if age < self.settings.ttl:
                self._in_scope(study)
                return study
        html, _ = await self.client.get(f"{BASE}/study/{study_id}", refresh)
        text = html.decode("utf-8", errors="replace")
        parsed = parse_study(text, study_id)
        extra = {}
        for key in ("methodological-aspects", "data-management"):
            if key not in parsed.tabs:
                raise RWEError(
                    "EMA_PARSE_FAILED", f"Missing {key} tab; cannot verify study scope/data types."
                )
            content, _ = await self.client.get(parsed.tabs[key], refresh)
            extra[key] = content.decode("utf-8", errors="replace")
        parsed = parse_study(text, study_id, extra["methodological-aspects"], extra["data-management"])
        parsed.data_source_types_source = parsed.tabs["data-management"]
        parsed.data_source_types_checked_at = parsed.detail_checked_at or now()
        # Persist current type even when excluded, so outdated CSV entries disappear from search.
        parsed.detail_checked_at = now()
        if study:
            # The detail pages are parsed for scope, types and documents only: fields that only the CSV
            # export supplies (medicines, conditions, outcomes, objective) must survive the refresh.
            for name in CATALOGUE_FIELDS:
                if not getattr(parsed, name):
                    setattr(parsed, name, getattr(study, name))
            if study.metadata_source.startswith("CSV"):
                # Source-type tags of an exported study come only from the typed exports (an empty set
                # means 'others'); the detail page's F8.7 values never replace them, on any later refresh.
                parsed.metadata_source = study.metadata_source
                parsed.data_source_types = study.data_source_types
                parsed.data_source_types_source = study.data_source_types_source
                parsed.data_source_types_checked_at = study.data_source_types_checked_at
        self.repo.upsert(parsed)
        self._in_scope(parsed)
        return parsed

    @staticmethod
    def _in_scope(study):
        if not is_non_interventional(study.study_type):
            raise RWEError(
                "STUDY_OUT_OF_SCOPE", "Only explicitly labelled Non-interventional studies are supported."
            )

    async def get_protocol(
        self, study_id: str, version: str = "latest", download: bool = True, refresh: bool = False
    ):
        study = await self.get_study(study_id, refresh)
        try:
            url = study.tabs.get("study-documents")
            if not url:
                raise RWEError("PROTOCOL_NOT_FOUND", "Study documents tab not found.")
            html, metadata = await self.client.get(url, refresh)
            docs = parse_documents(html.decode("utf-8", errors="replace"))
            protocol, reason = select_protocol(docs, version)
        except RWEError as exc:
            if exc.code == "PROTOCOL_NOT_FOUND" and version == "latest":
                self.repo.observe(study_id, protocol_found=False)  # ranked last from now on
            raise
        if version == "latest":
            self.repo.observe(study_id, protocol_found=True)
        result = {
            "study_id": study_id,
            "protocol": protocol.model_dump(),
            "selection_reason": reason,
            "available_protocols": [d.model_dump() for d in docs],
            "documents_checked_at": metadata["retrieved_at"],
            "cached": metadata["cached"],
        }
        if download:
            pdf, meta = await self.client.get(protocol.document_url, refresh)
            if not pdf.lstrip().startswith(b"%PDF-"):
                raise RWEError("DOCUMENT_DOWNLOAD_FAILED", "Selected document response is not a PDF.")
            result["download"] = meta
            archived = self.archive.save(
                study_id,
                pdf,
                {
                    **protocol.model_dump(),
                    "retrieved_at": meta["retrieved_at"],
                    "documents_checked_at": result["documents_checked_at"],
                    "study_url": study.source_url,
                },
            )
            # A cached PDF whose layer was observed for the same protocol is not parsed again.
            observed = self.repo.observation(study_id)
            same = meta.get("cached") and observed.get("protocol_id") == archived["protocol_id"]
            layer = (observed.get("text_layer") if same else None) or await asyncio.to_thread(text_layer, pdf)
            result["protocol"].update(
                protocol_id=archived["protocol_id"],
                local_filename=archived["local_filename"],
                text_layer=layer,
            )
            if (
                version == "latest"
                and layer
                and (observed.get("text_layer"), observed.get("protocol_id"))
                != (
                    layer,
                    archived["protocol_id"],
                )
            ):
                # Remembered so that later searches rank an image-only protocol after readable ones.
                self.repo.observe(study_id, text_layer=layer, protocol_id=archived["protocol_id"])
        return result

    async def _context(self, study_id: str, force_refresh: bool = False):
        result = await self.get_protocol(study_id, download=True, refresh=force_refresh)
        pdf, _ = self.archive.load(result["protocol"]["protocol_id"])
        # Include model/prompt/schema/parser identity; same URL with changed bytes invalidates the result.
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "url": result["protocol"]["document_url"],
                    "sha256": result["download"]["sha256"],
                    "version": result["protocol"]["version"],
                    "schema": SCHEMA_VERSION,
                    "parser": PARSER_VERSION,
                    "prompt": EXTRACTION_PROMPT,
                    "model": self.settings.llm_model or "client-assisted",
                    "provider": self.settings.llm_base_url,
                    "backend": self.settings.llm_backend,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        source = {
            "study_id": study_id,
            "study_url": f"{BASE}/study/{study_id}",
            **result["protocol"],
            "retrieved_at": result["download"]["retrieved_at"],
            "documents_checked_at": result["documents_checked_at"],
            "sha256": result["download"]["sha256"],
            "fingerprint": fingerprint,
            "selection_reason": result["selection_reason"],
            "extractor": self.settings.llm_model or "client-assisted",
            "schema_version": SCHEMA_VERSION,
            # The heading reading the sections are selected under ('' without translations). It stays out of
            # the fingerprint, which comparisons record before any translation; saves are checked against it.
            "reading": reading_hash(self._translations(result["protocol"]["protocol_id"])),
        }
        previous = self.repo.analysis(study_id)
        if previous and previous["source"]["fingerprint"] != fingerprint:
            self.repo.invalidate_analysis(self.repo.get(study_id))
        return pdf, source

    async def analyze_protocol(
        self,
        study_id: str,
        force_refresh: bool = False,
        offset: int = 0,
        max_chars: int = 30000,
        detail: str = "summary",
    ):
        if offset < 0 or not 6000 <= max_chars <= 150000:
            raise RWEError("INVALID_INPUT", "offset >= 0; max_chars must be 6000..150000.")
        if detail not in {"summary", "full"}:
            raise RWEError("INVALID_INPUT", "detail must be summary or full.")
        pdf, source = await self._context(study_id, force_refresh)
        old = self.repo.analysis(study_id)
        if old and force_refresh:
            # Subsequent client-extraction batches must not fall back to the old cached analysis.
            self.repo.invalidate_analysis(self.repo.get(study_id))
            old = None
        # An analysis saved before readings were recorded was read without translations ('').
        if (
            old
            and old["source"]["fingerprint"] == source["fingerprint"]
            and old["source"].get("reading", "") == source["reading"]
        ):
            old["source"].update(
                retrieved_at=source["retrieved_at"], documents_checked_at=source["documents_checked_at"]
            )
            try:
                self.repo.save_analysis(self.repo.get(study_id), old, reading=source["reading"])
                return analysis_view(dict(old, cached=True), detail)
            except RWEError as exc:
                if exc.code != "READING_CONTEXT_CHANGED":
                    raise
                source["reading"] = reading_hash(self._translations(source["protocol_id"]))
        if stored_translations(self.explorer.reading_record(source["protocol_id"])) is None:
            request = await self._check_heading_language(source, pdf)
            if request:
                return request
            source["reading"] = reading_hash(self._translations(source["protocol_id"]))
        if configured(self.settings):
            return analysis_view(await self._provider_extraction(study_id, pdf, source), detail)
        # One snapshot of the translations: the batch is read under the reading it reports.
        translations = self._translations(source["protocol_id"])
        source["reading"] = reading_hash(translations)
        pages = await asyncio.to_thread(extract_pages, pdf, True, translations)
        chunks = reading_order(sections(pages))
        if not chunks:
            raise RWEError("SECTION_PARSE_FAILED", "No relevant protocol sections found.")
        selected, size = [], 0
        for chunk in chunks[offset:]:
            if selected and size + len(chunk["text"]) > max_chars:
                break
            selected.append(chunk)
            size += len(chunk["text"])
        next_offset = offset + len(selected)
        result = {
            "status": "needs_client_extraction",
            "source": source,
            "cached": False,
            "cached_batch_offsets": sorted(
                self._partials.get((study_id, source["fingerprint"], source["reading"]), {})
            ),
            "sections": selected,
            "offset": offset,
            "next_offset": next_offset if next_offset < len(chunks) else None,
            "total_sections": len(chunks),
            "total_pdf_pages": len(pages),
        }
        if offset == 0:
            # The schema and instructions travel once; later batches carry only sections.
            result.update(
                instruction=EXTRACTION_PROMPT + " Read every batch using next_offset (with a 200k+ context "
                "window pass max_chars=120000 to cut round trips). After each batch call "
                "cache_protocol_analysis with fingerprint, reading (source.reading), that batch's analysis and "
                "batch_offset=offset so "
                "progress survives context compaction (skip offsets already in cached_batch_offsets); after "
                "the last batch call it with coverage_complete=true to merge and save. Later batches omit "
                "this instruction and analysis_schema.",
                analysis_schema=Extraction.model_json_schema(),
                selection_coverage="Keyword/heading selected sections; unselected text may contain additional facts.",
                exploration_tools=[
                    "get_protocol_outline",
                    "search_protocol_text",
                    "read_protocol_text",
                    "research_protocol",
                ],
            )
        return result

    def _translations(self, protocol_id: str) -> dict[str, str]:
        return self.explorer.translations(protocol_id)

    def _reading_snapshot(self, source: dict, reading: str | None, redo: str) -> dict[str, str]:
        """The current translations, after checking that a caller's work was read under them: required for a
        non-English protocol (an English one has none, so its callers may omit reading). Sets source.reading."""
        translations = self._translations(source["protocol_id"])
        source["reading"] = reading_hash(translations)
        record = self.explorer.reading_record(source["protocol_id"])
        if record and record.get("language") == "other" and reading != source["reading"]:
            raise RWEError(
                "READING_CONTEXT_CHANGED",
                "This work was not read under the protocol's current heading translations (pass the reading "
                f"returned with it); read the protocol again and {redo}.",
            )
        return translations

    async def _pages(self, pdf: bytes, protocol_id: str):
        return await asyncio.to_thread(extract_pages, pdf, True, self._translations(protocol_id))

    async def _check_heading_language(self, source: dict, pdf: bytes) -> dict | None:
        """Record whether the protocol is in English. Section roles are read from English heading words, so a
        non-English protocol's headings are translated first: by the configured LLM, else by the caller."""
        protocol_id = source["protocol_id"]
        pages = await asyncio.to_thread(extract_pages, pdf)
        record = {"parser": PARSER_VERSION, "language": "english", "translations": {}}
        if not english(pages):
            headings = heading_texts(sections(pages))
            if not configured(self.settings):
                return {
                    "status": "needs_heading_translation",
                    "study_id": source["study_id"],
                    "protocol_id": protocol_id,
                    "source": source,
                    "headings": headings,
                    "instruction": "This protocol is not in English, and section roles are read from English "
                    "heading words. Translate every heading into English (keep the numbering; translate only), "
                    "call cache_heading_translations with protocol_id and translations {heading: English}, "
                    "then call analyze_protocol again. If you cannot translate, pass translations={}: every "
                    "section with an unrecognised heading is then read.",
                }
            try:
                translations = await translate_headings(self.settings, headings)
            except RWEError as exc:
                retry = " Call analyze_protocol again." if exc.code == "LLM_EXTRACTION_FAILED" else ""
                raise RWEError(
                    exc.code,
                    f"Heading translation for this non-English protocol failed: {exc.message}{retry}",
                ) from exc
            record.update(language="other", translations=translations, translator=self.settings.llm_model)
        study = self.repo.get(source["study_id"])
        # keep_parser: translations a caller saved (in another process) during this check are not overwritten.
        self.repo.set_reading(
            protocol_id, record, reading_hash(record["translations"]), study, keep_parser=PARSER_VERSION
        )
        return None

    async def cache_heading_translations(self, protocol_id: str, translations: dict[str, str]):
        """Save a caller's English translations of a non-English protocol's headings (see analyze_protocol)."""
        pdf, _ = self.archive.load(protocol_id)
        pages = await asyncio.to_thread(extract_pages, pdf)
        if english(pages):
            raise RWEError("INVALID_INPUT", "This protocol is in English; its headings need no translation.")
        headings = heading_texts(sections(pages))
        kept = clean_translations(headings, translations)
        study_id = protocol_id.split("_")[1]
        record = {
            "parser": PARSER_VERSION,
            "language": "other",
            "translations": kept,
            "translator": "client-assisted",
        }
        # Translations decide the sections read. They stay out of the fingerprint, which comparisons record
        # before any translation; instead the database retires this PDF's analysis in the same transaction
        # that saves a changed reading, and every later save is checked against the reading (also across
        # server processes sharing the database).
        if self.repo.set_reading(protocol_id, record, reading_hash(kept), self.repo.get(study_id)):
            # This process's held batches and server-side extractions (running or uncollected) read the
            # old way: drop them rather than let them fail at save time.
            for key in [k for k in self._partials if k[0] == study_id]:
                self._partials.pop(key)
            for key in [k for k in self._extractions if k[0] == study_id]:
                self._extractions.pop(key)[0].cancel()
        return {
            "status": "translations_cached",
            "study_id": study_id,
            "protocol_id": protocol_id,
            "translated_headings": len(kept),
            "total_headings": len(headings),
            "ignored_entries": len(translations) - len(kept),
            "instruction": "Call analyze_protocol again for this study.",
        }

    async def plan_study_search(self, question: str, use_llm: bool = False):
        expansion = expand(question)
        clinical = expansion["clinical"]
        logged = record_unmatched(self.unmatched_log_path, question, "plan_study_search", expansion)
        queries = list(
            dict.fromkeys(
                clinical["english_terms"]
                + clinical["related_terms"]
                + [
                    v
                    for c in clinical["code_candidates"]
                    if c["origin"] != "official_dictionary"  # an EMA record's code is no catalogue query
                    for v in c["search_variants"]
                ]
            )
        )
        if not queries:
            queries = expansion["synonyms"] or ([question] if question.isascii() else [])
        # Design words (cohort, 傾向スコア) translate locally, but the clinical concept of a Japanese
        # question is known only when a dictionary concept matched.
        untranslated = not question.isascii() and not clinical["concepts"]
        plan = {
            "question": question,
            "query_expansion": expansion,
            "queries": queries,
            "target_role": "outcome"
            if "アウトカム" in question or "outcome" in question.lower()
            else "unspecified",
            "status": "needs_client_translation" if untranslated or not queries else "planned",
            "unmatched_logged": logged,
            "dictionaries": clinical["dictionaries"],
            "client_expansion": {
                "required": untranslated or not queries,
                "instruction": CLIENT_EXPANSION_INSTRUCTION,
            },
            "code_systems_to_check": clinical["systems_to_check"],
            "analogous_terms": clinical["analogous_terms"],
            "instruction": "Search English names, related names and codes independently. Then inspect outcome/exposure definitions, "
            "code lists and appendices in the selected PDFs. Check the source database, vocabulary/version, "
            "code-set membership and algorithm (counts, time windows, exclusions). A code hit alone does not prove outcome use.",
            "method": "user_dictionary" if clinical["concepts"] else "client_expansion",
            "note": "Review synonyms for ambiguity; search multiple formulations and compare results.",
        }
        if use_llm:
            result = await complete_json(
                self.settings,
                [
                    {
                        "role": "system",
                        "content": "Plan EMA RWD protocol searches. Translate the user's clinical concepts into English. "
                        "Return JSON {queries: [up to 5 English keyword/code queries], synonyms: [up to 30 English search phrases], "
                        "code_candidates: [{system, code, label, vocabulary_version, relation, source_url}], "
                        "analogous_terms: [{term, relation}], uncertainties: [strings]}. "
                        "For code_candidates, relation is candidate/related/broader/narrower/unspecified_subtype; version, label "
                        "and URL may be null. analogous_terms are up to 10 clinically analogous but different English concepts, "
                        "searched only when the requested one has no study; their relation is only broader (category), sibling "
                        "(another disease of the same category) or associated (complication or related condition). "
                        "Consider ICD-10 and national modifications, ICD-9-CM, SNOMED CT, Read/CTV3, MedDRA, OMOP, ATC, RxNorm, "
                        "NDC, LOINC or local codes according to source data. Do not invent codes/URLs: omit uncertain codes and "
                        "describe required master lookup. Suggestions are unverified retrieval candidates, not study definitions. "
                        "Include separate name-based and code-based queries. Distinguish outcome vs exposure vs comorbidity. "
                        + ICD10_SYNONYM_GUIDANCE
                        + " "
                        "For medicines, translate non-English names, expand brand names to International Nonproprietary Names "
                        "and INNs to brand names in BOTH directions, and suggest ATC codes when known. Put translated "
                        "drug names in synonyms so the local official EMA dictionary can verify and expand them. "
                        "Preserve full ingredient sets for combination products, salts, formulations and country differences. "
                        "Do not equate a broad disease with an unspecified subtype or replace a class with one drug. No completeness claim.",
                    },
                    {"role": "user", "content": question},
                ],
            )
            queries = result.get("queries")
            if (
                not isinstance(queries, list)
                or not 1 <= len(queries) <= 5
                or any(
                    not isinstance(q, str) or not q.strip() or len(q) > 500 or not q.isascii()
                    for q in queries
                )
            ):
                raise RWEError(
                    "SCHEMA_VALIDATION_FAILED", "Search planner must return 1..5 bounded query strings."
                )
            extra = result.get("synonyms", [])
            if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
                raise RWEError("SCHEMA_VALIDATION_FAILED", "Search planner synonyms must be strings.")
            plan.update(
                method="llm_query_plan",
                queries=queries,
                query_expansion=expand(question, extra, proposed_codes(result.get("code_candidates", []))),
                status="planned",
                uncertainties=result.get("uncertainties", []),
            )
            plan["client_expansion"]["required"] = False  # the server LLM already translated
            sources = plan["query_expansion"]["term_sources"]
            for term in extra:
                if sources.get(term) == "caller":
                    sources[term] = "llm"
            try:
                proposed = [AnalogousTerm.model_validate(a) for a in result.get("analogous_terms") or []][:10]
            except (ValidationError, TypeError) as exc:
                raise RWEError(
                    "SCHEMA_VALIDATION_FAILED", "Search planner analogous_terms are invalid."
                ) from exc
            plan["analogous_terms"] = analogous_terms([plan["query_expansion"]], proposed)
        plan["queries"] = list(
            dict.fromkeys(
                [
                    *plan["queries"],
                    *plan["query_expansion"]["clinical"]["english_terms"],
                    *plan["query_expansion"]["clinical"]["related_terms"],
                ]
            )
        )
        plan["code_searches"] = [
            {"query": c["code"], "codes": [{k: v for k, v in c.items() if k != "search_variants"}]}
            for c in plan["query_expansion"]["clinical"]["code_candidates"]
            if c["origin"] != "official_dictionary"  # catalogue searches never key on an EMA record's code
        ]
        return plan

    async def list_local_protocols(self, study_id: str):
        return {"study_id": study_id, "protocols": self.archive.list(study_id), "network_requests": 0}

    async def get_protocol_outline(
        self, protocol_id: str, offset: int = 0, limit: int = 100, detail: str = "compact"
    ):
        return await asyncio.to_thread(self.explorer.outline, protocol_id, offset, limit, detail)

    async def search_protocol_text(
        self,
        protocol_id: str,
        query: str,
        limit: int = 10,
        synonyms=None,
        codes=None,
        max_chars: int | None = None,
    ):
        return await asyncio.to_thread(
            self.explorer.search, protocol_id, query, limit, synonyms, codes, max_chars
        )

    async def read_protocol_text(
        self,
        protocol_id: str,
        section_id: str | None = None,
        start_page: int = 1,
        end_page: int | None = None,
        offset: int = 0,
        max_chars: int = 12000,
    ):
        return await asyncio.to_thread(
            self.explorer.read, protocol_id, section_id, start_page, end_page, offset, max_chars
        )

    async def research_protocol(self, protocol_id: str, question: str, force: bool = False):
        return await self.explorer.research(protocol_id, question, force)

    async def cache_protocol_answer(
        self, protocol_id: str, question: str, answer: ProtocolAnswer, reading: str | None = None
    ):
        """Cache a caller's answer under the reading research_protocol returned with the question."""
        source = {"protocol_id": protocol_id}
        self._reading_snapshot(source, reading, "answer the question again")
        return await asyncio.to_thread(
            self.explorer.save, protocol_id, question, answer, None, source["reading"]
        )

    async def cache_protocol_analysis(
        self,
        study_id: str,
        fingerprint: str,
        analysis: Extraction,
        coverage_complete: bool = False,
        batch_offset: int | None = None,
        reading: str | None = None,
    ):
        """Save a caller extraction. With batch_offset the batch is held server-side until the final
        coverage_complete call merges every cached batch, so context compaction cannot lose progress.
        reading is analyze_protocol's source.reading: a non-English protocol's batches must have been read
        under its current heading translations."""
        if not coverage_complete and batch_offset is None:
            raise RWEError(
                "INCOMPLETE_EXTRACTION",
                "Read all section batches before caching, or cache each batch with batch_offset first.",
            )
        pdf, source = await self._context(study_id)
        if source["fingerprint"] != fingerprint:
            raise RWEError("PROTOCOL_CHANGED", "Protocol/extractor changed; analyze current protocol again.")
        translations = self._reading_snapshot(source, reading, "reread the batches")
        pages = await asyncio.to_thread(extract_pages, pdf, True, translations)
        validate_evidence(analysis, pages)
        key = (study_id, fingerprint, source["reading"])
        if not coverage_complete:
            self._partials.setdefault(key, {})[batch_offset] = analysis
            return {
                "status": "batch_cached",
                "study_id": study_id,
                "cached_batch_offsets": sorted(self._partials[key]),
            }
        partials = self._partials.pop(key, {})
        if partials:
            analysis = merge_extractions([*partials.values(), analysis])
        notes = audit_extraction(analysis, pages, self.repo.get(study_id).catalogue_data_sources)
        if notes:
            analysis.missing_information = with_notes(analysis.missing_information, notes)
        return self._save(study_id, analysis, source, "client_assisted", detail="summary")

    async def _provider_extraction(self, study_id: str, pdf: bytes, source: dict) -> dict:
        """Run (or keep running) the server-side extraction; return status=extracting when it outlasts the wait."""
        key = (study_id, source["fingerprint"], source["reading"])
        for stale in [k for k in self._extractions if k[0] == study_id and k != key]:
            # The protocol or its reading changed under a running extraction: its result would be stale.
            self._extractions.pop(stale)[0].cancel()
        entry = self._extractions.get(key)
        if entry is None or (entry[0].done() and entry[0].exception() is not None):
            progress: dict = {}
            task = asyncio.create_task(self._extract_and_save(study_id, pdf, source, progress))
            entry = self._extractions[key] = (task, progress)
        task, progress = entry
        done, _ = await asyncio.wait({task}, timeout=self.settings.llm_wait_seconds)
        if task in done:
            self._extractions.pop(key, None)
            if task.cancelled():
                raise RWEError(
                    "PROTOCOL_CHANGED", "Protocol changed during extraction; call analyze_protocol again."
                )
            return task.result()
        return {
            "status": "extracting",
            "study_id": study_id,
            "source": source,
            "cached": False,
            "progress": dict(progress),
            "instruction": "Server-side extraction is still running; call analyze_protocol again for this "
            "study to collect the cached result. Do not extract client-side.",
        }

    async def _extract_and_save(self, study_id: str, pdf: bytes, source: dict, progress: dict) -> dict:
        translations = self._translations(source["protocol_id"])
        if reading_hash(translations) != source["reading"]:
            raise RWEError(
                "READING_CONTEXT_CHANGED", "Heading translations changed; call analyze_protocol again."
            )
        # The task reads with this snapshot of the translations; its save is checked against the same reading.
        pages = await asyncio.to_thread(extract_pages, pdf, True, translations)
        chunks = reading_order(sections(pages))
        if not chunks:
            raise RWEError("SECTION_PARSE_FAILED", "No relevant protocol sections found.")
        analysis = await extract_with_provider(self.settings, chunks, progress)
        catalogue_sources = self.repo.get(study_id).catalogue_data_sources
        analysis, _ = await asyncio.to_thread(finalize_extraction, analysis, pages, catalogue_sources)
        validate_evidence(analysis, pages)
        return self._save(study_id, analysis, source, "configured_provider")

    def _save(self, study_id, analysis, source, method, detail: str = "full"):
        result = {
            "status": "analyzed",
            "study_id": study_id,
            "analysis": analysis.model_dump(),
            "source": source,
            "analyzed_at": now(),
            "extraction_method": method,
            "validation": "Exact quote/page and optional section verified; semantic correctness requires review.",
            "cached": False,
        }
        self.repo.save_analysis(self.repo.get(study_id), result, reading=source.get("reading", ""))
        return analysis_view(result, detail)


def analysis_view(result: dict, detail: str) -> dict:
    """Caller-facing view of an analyzed result: counts and data source names by default, the whole
    extraction only with detail=full (comparisons read the stored JSON, not this response)."""
    if detail == "full" or result.get("status") != "analyzed":
        return result
    analysis = result["analysis"]
    summary = {
        name: (len(value) if isinstance(value, list) else (1 if value else 0))
        for name, value in analysis.items()
        if name != "schema_version"
    }
    return {
        **{k: v for k, v in result.items() if k != "analysis"},
        "analysis_summary": summary,
        "data_sources": [
            {"value": d["value"], "usage": d["usage"]} for d in analysis.get("data_sources", [])
        ],
        "missing_information": analysis.get("missing_information", []),
        "note": "Full extraction: analyze_protocol(detail='full'); comparisons already include it.",
    }
