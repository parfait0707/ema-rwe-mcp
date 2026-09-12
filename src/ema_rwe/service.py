import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime

from .archive import ProtocolArchive
from .config import Settings
from .domain import CodeCandidate, Extraction, ProtocolAnswer, RWEError, now
from .drugs import refresh_dictionary
from .ema import BASE, is_non_interventional, parse_documents, parse_study, select_protocol
from .exploration import Explorer
from .http import EMAClient
from .llm import EXTRACTION_PROMPT, complete_json, configured, extract_with_provider
from .pdf import extract_pages, sections, validate_evidence
from .storage import Repository
from .terminology import proposed_codes
from .vocabulary import expand


class Service:
    def __init__(self, settings: Settings | None = None, client=None, repo=None):
        self.settings = settings or Settings()
        self.repo = repo or Repository(self.settings.db_path)
        self.client = client or EMAClient(self.settings)
        self.archive = ProtocolArchive(
            self.settings.protocol_dir or self.settings.db_path.parent / "protocols"
        )
        self.explorer = Explorer(self.settings, self.archive, self.repo)

    async def close(self):
        await self.client.close()

    async def refresh_drug_dictionary(self, force: bool = False):
        return await refresh_dictionary(force)

    def search_studies(
        self,
        query: str,
        limit: int = 5,
        darwin_only: bool = True,
        status: list[str] | None = None,
        analyzed_only: bool = False,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
    ):
        results = self.repo.search(query, limit, darwin_only, status, analyzed_only, synonyms, codes)
        for result in results:
            result["protocol_id"] = (result.get("protocol_source") or {}).get("protocol_id")
            result["local_protocols"] = [
                {k: r.get(k) for k in ("protocol_id", "local_filename", "version", "document_url")}
                for r in self.archive.list(result["study_id"])
            ]
        return {
            "query": query,
            "results": results,
            "query_expansion": expand(query, synonyms, codes),
            "search_mode": "local_fts_bm25",
            "network_requests": 0,
            "note": "Protocol fields are not_analyzed until analyze_protocol and extraction are completed. "
            "Use analyzed_only=true for previously extracted protocols. Latest is as-of protocol_source; "
            "call analyze_protocol to check for updates. Inspect query_expansion and add synonyms or call plan_study_search.",
        }

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
                    "schema": "0.1",
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
            "schema_version": "0.1",
        }
        previous = self.repo.analysis(study_id)
        if previous and previous["source"]["fingerprint"] != fingerprint:
            self.repo.invalidate_analysis(self.repo.get(study_id))
        return pdf, source

    async def analyze_protocol(
        self, study_id: str, force_refresh: bool = False, offset: int = 0, max_chars: int = 30000
    ):
        if offset < 0 or not 6000 <= max_chars <= 60000:
            raise RWEError("INVALID_INPUT", "offset >= 0; max_chars must be 6000..60000.")
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
            return dict(old, cached=True)
        pages = await asyncio.to_thread(extract_pages, pdf)
        chunks = [c for c in sections(pages) if c["relevant"]]
        if not chunks:
            raise RWEError("SECTION_PARSE_FAILED", "No relevant protocol sections found.")
        if configured(self.settings):
            analysis = await extract_with_provider(self.settings, chunks)
            validate_evidence(analysis, pages)
            return self._save(study_id, analysis, source, "configured_provider")
        selected, size = [], 0
        for chunk in chunks[offset:]:
            if selected and size + len(chunk["text"]) > max_chars:
                break
            selected.append(chunk)
            size += len(chunk["text"])
        next_offset = offset + len(selected)
        return {
            "status": "needs_client_extraction",
            "source": source,
            "cached": False,
            "instruction": EXTRACTION_PROMPT + " Read every batch using next_offset; then call "
            "cache_protocol_analysis with fingerprint, analysis and coverage_complete=true.",
            "analysis_schema": Extraction.model_json_schema(),
            "sections": selected,
            "offset": offset,
            "next_offset": next_offset if next_offset < len(chunks) else None,
            "total_sections": len(chunks),
            "total_pdf_pages": len(pages),
            "selection_coverage": "Keyword/heading selected sections; unselected text may contain additional facts.",
            "exploration_tools": [
                "get_protocol_outline",
                "search_protocol_text",
                "read_protocol_text",
                "research_protocol",
            ],
        }

    async def plan_study_search(self, question: str, use_llm: bool = False):
        expansion = expand(question)
        clinical = expansion["clinical"]
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

    async def get_protocol_outline(self, protocol_id: str, offset: int = 0, limit: int = 100):
        return await asyncio.to_thread(self.explorer.outline, protocol_id, offset, limit)

    async def search_protocol_text(
        self, protocol_id: str, query: str, limit: int = 10, synonyms=None, codes=None
    ):
        return await asyncio.to_thread(self.explorer.search, protocol_id, query, limit, synonyms, codes)

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
        self, study_id: str, fingerprint: str, analysis: Extraction, coverage_complete: bool = False
    ):
        if not coverage_complete:
            raise RWEError(
                "INCOMPLETE_EXTRACTION", "Read all returned section batches before caching an analysis."
            )
        pdf, source = await self._context(study_id)
        if source["fingerprint"] != fingerprint:
            raise RWEError("PROTOCOL_CHANGED", "Protocol/extractor changed; analyze current protocol again.")
        pages = await asyncio.to_thread(extract_pages, pdf)
        validate_evidence(analysis, pages)
        return self._save(study_id, analysis, source, "client_assisted")

    def _save(self, study_id, analysis, source, method):
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
        return result
