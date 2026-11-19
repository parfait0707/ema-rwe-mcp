"""Protocol retrieval: study pages, latest-protocol selection, the extraction context and PDF reading."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime

from .domain import (
    Extraction,
    RWEError,
    now,
)
from .ema import BASE, is_non_interventional, parse_documents, parse_study, select_protocol
from .llm import (
    EXTRACTION_PROMPT,
)
from .pdf import (
    PARSER_VERSION,
    reading_hash,
    text_layer,
)

SCHEMA_VERSION = Extraction.model_fields["schema_version"].default
# Study fields the detail-page parser does not (fully) read; a page refresh keeps the stored values.
CATALOGUE_FIELDS = (
    "exposures",
    "conditions",
    "outcomes",
    "objective",
    "catalogue_data_sources",
    "protocol_listed",
)


class ProtocolsMixin:
    """Service methods for retrieving and reading protocols; uses settings, repo, client, archive and explorer."""

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
