import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .archive import ProtocolArchive
from .comparison import Comparisons
from .config import Settings
from .domain import CodeCandidate, Extraction, ProtocolAnswer, RWEError, SourcePreference, now
from .drugs import refresh_dictionary
from .ema import BASE, is_non_interventional, parse_documents, parse_study, select_protocol
from .exploration import Explorer
from .http import EMAClient
from .llm import EXTRACTION_PROMPT, complete_json, configured, extract_with_provider, merge_extractions
from .pdf import extract_pages, prune_unverifiable, reading_order, sections, validate_evidence
from .selection import TYPED_SOURCES, SearchFilters, compact, selection
from .source_types import preference_for
from .storage import Repository, import_csv
from .terminology import proposed_codes
from .vocabulary import expand


def record_unmatched(path: Path, text: str, tool: str, expansion: dict) -> bool:
    """Log a non-English query that neither the concept dictionary nor the drug dictionary recognised.

    The log lives beside the database (not inside it, which is committed) and is the evidence for
    deciding which concepts to add to data/terminology.json. English-only queries are not logged:
    the index is English, so they need no translation.
    """
    text = " ".join(text.split())[:200]
    clinical = expansion["clinical"]
    if (
        not text
        or text.isascii()
        or expansion["concepts"]
        or clinical["concepts"]
        or clinical["drugs"]["total_matches"]
    ):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        log = {}
    entry = log.setdefault(text, {"count": 0, "first_seen": now(), "tools": []})
    entry["count"] += 1
    entry["last_seen"] = now()
    if tool not in entry["tools"]:
        entry["tools"].append(tool)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True


def unmatched_summary(path: Path, limit: int = 20) -> dict:
    """Most frequent unrecognised queries, for deciding whether the dictionary needs new concepts."""
    try:
        log = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        log = {}
    ranked = sorted(log.items(), key=lambda kv: (-kv[1]["count"], kv[1].get("last_seen", "")))
    return {
        "path": str(path),
        "distinct": len(log),
        "top": [{"text": text, **entry} for text, entry in ranked[:limit]],
        "note": "Japanese queries that matched no concept or medicine. Add frequent ones to "
        "data/terminology.json, then derive related terms with scripts/mine_terminology.py.",
    }


def expansion_summary(expansion: dict) -> dict:
    """The parts of a query expansion a caller acts on; plan_study_search returns the full form."""
    clinical = expansion["clinical"]
    return {
        "synonyms": expansion["synonyms"],
        "english_terms": clinical["english_terms"],
        "related_terms": clinical["related_terms"],
        "codes": [f"{c['system']}:{c['code']}" for c in clinical["code_candidates"]],
        "drug_terms": clinical["drugs"]["english_terms"],
        "drugs_need_refresh": clinical["drugs"]["needs_refresh"],
    }


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
        self._extractions: dict[str, tuple[asyncio.Task, dict]] = {}
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
        if latest:
            age = (datetime.now(UTC) - datetime.fromisoformat(latest["imported_at"])).total_seconds()
            state = "current" if age <= self.settings.catalogue_ttl else "stale"
        else:
            age, state = None, "not_imported"
        return {
            "status": state,
            "latest_import": latest,
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
            "browser_refresh_recommended": state != "current",
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
        darwin_only: bool = True,
        status: list[str] | None = None,
        analyzed_only: bool = False,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
        filters: SearchFilters | None = None,
        role: str = "any",
        detail: str = "compact",
    ):
        if not 1 <= limit <= 20:
            raise RWEError("INVALID_INPUT", "limit must be 1..20; preview also obeys max_comparison_studies.")
        if detail not in ("compact", "full"):
            raise RWEError("INVALID_INPUT", "detail must be compact or full.")
        candidates = self.repo.search(
            query, None, darwin_only, status, analyzed_only, synonyms, codes, filters, role
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
    ):
        if (
            not question.strip()
            or len(question) > 2000
            or not 1 <= len(queries) <= 20
            or any(not q.strip() for q in queries)
        ):
            raise RWEError("INVALID_INPUT", "A question and 1..20 nonempty search queries are required.")
        preference = preference_for(filters, source_preference)
        unique = {}
        for query in queries:
            for row in self.repo.search(
                query, None, darwin_only, None, False, synonyms, codes, filters, role
            ):
                unique.setdefault(row["study_id"], row)
        candidates = list(unique.values())
        for query in queries:
            record_unmatched(
                self.unmatched_log_path, query, "compare_protocols", expand(query, synonyms, codes)
            )
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
            "selected_study_ids": study_ids,
            "filters": (filters or SearchFilters()).model_dump(),
            "query_expansions": [expansion_summary(expand(q, synonyms, codes)) for q in queries],
            "darwin_only": darwin_only,
            "search_scope": "local catalogue metadata and saved analysis only",
            **self._catalogue_summary(bool(candidates)),
        }
        if not 1 <= len(candidates) <= self.settings.max_screening_studies:
            return {**search, "network_requests": 0, "rows": [], "pdf_downloads": 0}
        return await self.comparisons.prepare(question, candidates, search)

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
        url = study.tabs.get("study-documents")
        if not url:
            raise RWEError("PROTOCOL_NOT_FOUND", "Study documents tab not found.")
        html, metadata = await self.client.get(url, refresh)
        docs = parse_documents(html.decode("utf-8", errors="replace"))
        protocol, reason = select_protocol(docs, version)
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
            result["protocol"].update(
                protocol_id=archived["protocol_id"], local_filename=archived["local_filename"]
            )
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
                    "schema": "0.3",
                    "parser": "structural-v4",
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
            "schema_version": "0.3",
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
        if old and old["source"]["fingerprint"] == source["fingerprint"] and not force_refresh:
            old["source"].update(
                retrieved_at=source["retrieved_at"], documents_checked_at=source["documents_checked_at"]
            )
            self.repo.save_analysis(self.repo.get(study_id), old)
            return analysis_view(dict(old, cached=True), detail)
        if configured(self.settings):
            return analysis_view(await self._provider_extraction(study_id, pdf, source), detail)
        pages = await asyncio.to_thread(extract_pages, pdf)
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
            "cached_batch_offsets": sorted(self._partials.get((study_id, source["fingerprint"]), {})),
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
                "cache_protocol_analysis with fingerprint, that batch's analysis and batch_offset=offset so "
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

    async def plan_study_search(self, question: str, use_llm: bool = False):
        expansion = expand(question)
        clinical = expansion["clinical"]
        logged = record_unmatched(self.unmatched_log_path, question, "plan_study_search", expansion)
        queries = list(
            dict.fromkeys(
                clinical["english_terms"]
                + clinical["related_terms"]
                + [v for c in clinical["code_candidates"] for v in c["search_variants"]]
            )
        )
        if not queries:
            queries = expansion["synonyms"] or ([question] if question.isascii() else [])
        plan = {
            "question": question,
            "query_expansion": expansion,
            "queries": queries,
            "target_role": "outcome"
            if "アウトカム" in question or "outcome" in question.lower()
            else "unspecified",
            "status": "planned" if queries else "needs_client_translation",
            "unmatched_logged": logged,
            "code_systems_to_check": clinical["systems_to_check"],
            "instruction": "Search English names, related names and codes independently. Then inspect outcome/exposure definitions, "
            "code lists and appendices in the selected PDFs. Check the source database, vocabulary/version, "
            "code-set membership and algorithm (counts, time windows, exclusions). A code hit alone does not prove outcome use.",
            "method": "curated_synonyms",
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
                        "code_candidates: [{system, code, label, vocabulary_version, relation, source_url}], uncertainties: [strings]}. "
                        "relation is candidate/related/broader/narrower/unspecified_subtype; version, label and URL may be null. "
                        "Consider ICD-10 and national modifications, ICD-9-CM, SNOMED CT, Read/CTV3, MedDRA, OMOP, ATC, RxNorm, "
                        "NDC, LOINC or local codes according to source data. Do not invent codes/URLs: omit uncertain codes and "
                        "describe required master lookup. Suggestions are unverified retrieval candidates, not study definitions. "
                        "Include separate name-based and code-based queries. Distinguish outcome vs exposure vs comorbidity. "
                        "For medicines, translate Japanese names, expand brand names to International Nonproprietary Names "
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

    async def cache_protocol_answer(self, protocol_id: str, question: str, answer: ProtocolAnswer):
        return await asyncio.to_thread(self.explorer.save, protocol_id, question, answer)

    async def cache_protocol_analysis(
        self,
        study_id: str,
        fingerprint: str,
        analysis: Extraction,
        coverage_complete: bool = False,
        batch_offset: int | None = None,
    ):
        """Save a caller extraction. With batch_offset the batch is held server-side until the final
        coverage_complete call merges every cached batch, so context compaction cannot lose progress."""
        if not coverage_complete and batch_offset is None:
            raise RWEError(
                "INCOMPLETE_EXTRACTION",
                "Read all section batches before caching, or cache each batch with batch_offset first.",
            )
        pdf, source = await self._context(study_id)
        if source["fingerprint"] != fingerprint:
            raise RWEError("PROTOCOL_CHANGED", "Protocol/extractor changed; analyze current protocol again.")
        pages = await asyncio.to_thread(extract_pages, pdf)
        validate_evidence(analysis, pages)
        key = (study_id, fingerprint)
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
        return self._save(study_id, analysis, source, "client_assisted", detail="summary")

    async def _provider_extraction(self, study_id: str, pdf: bytes, source: dict) -> dict:
        """Run (or keep running) the server-side extraction; return status=extracting when it outlasts the wait."""
        entry = self._extractions.get(study_id)
        if entry is None or (entry[0].done() and entry[0].exception() is not None):
            progress: dict = {}
            task = asyncio.create_task(self._extract_and_save(study_id, pdf, source, progress))
            entry = self._extractions[study_id] = (task, progress)
        task, progress = entry
        done, _ = await asyncio.wait({task}, timeout=self.settings.llm_wait_seconds)
        if task in done:
            self._extractions.pop(study_id, None)
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
        pages = await asyncio.to_thread(extract_pages, pdf)
        chunks = reading_order(sections(pages))
        if not chunks:
            raise RWEError("SECTION_PARSE_FAILED", "No relevant protocol sections found.")
        analysis = await extract_with_provider(self.settings, chunks, progress)
        analysis, dropped = await asyncio.to_thread(prune_unverifiable, analysis, pages)
        if dropped:
            analysis.missing_information = analysis.missing_information[:29] + [
                f"{dropped} provider evidence item(s) failed verbatim/page verification and were dropped."
            ]
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
        self.repo.save_analysis(self.repo.get(study_id), result)
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
