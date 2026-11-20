"""Protocol extraction and question answers: caller batches, server-side extraction, heading readings."""

import asyncio
import hashlib

from .domain import (
    Extraction,
    ProtocolAnswer,
    RWEError,
    now,
    with_notes,
)
from .llm import (
    EXTRACTION_PROMPT,
    configured,
    extract_with_provider,
    merge_extractions,
    translate_headings,
)
from .pdf import (
    PARSER_VERSION,
    audit_extraction,
    clean_translations,
    compact_spaces,
    english,
    extract_pages,
    finalize_extraction,
    heading_texts,
    reading_hash,
    reading_order,
    sections,
    stored_translations,
    validate_evidence,
    with_corrected_pages,
    with_source_name_quotes,
)


class ExtractionMixin:
    """Service methods for extraction and answers; uses settings, repo, archive, explorer and _context."""

    async def analyze_protocol(
        self,
        study_id: str,
        force_refresh: bool = False,
        offset: int = 0,
        max_chars: int = 30000,
        detail: str = "summary",
        question: str | None = None,
        on_progress=None,
    ):
        if question is not None and (not question.strip() or len(question) > 2000):
            raise RWEError("INVALID_INPUT", "Question must be 1..2000 characters.")
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
            return analysis_view(await self._provider_extraction(study_id, pdf, source, on_progress), detail)
        # One snapshot of the translations: the batch is read under the reading it reports.
        translations = self._translations(source["protocol_id"])
        source["reading"] = reading_hash(translations)
        pages = await asyncio.to_thread(extract_pages, pdf, True, translations)
        chunks = compact_spaces(reading_order(sections(pages)))
        if not chunks:
            raise RWEError("SECTION_PARSE_FAILED", "No relevant protocol sections found.")
        selected, size = [], 0
        for chunk in chunks[offset:]:
            if selected and size + len(chunk["text"]) > max_chars:
                break
            selected.append(chunk)
            size += len(chunk["text"])
        next_offset = offset + len(selected)
        # Which sections each batch held, so the final save can tell a batch that was never read.
        self.repo.save_batch(
            study_id, source["fingerprint"], source["reading"], "served", offset, {"next": next_offset}
        )
        result = {
            "status": "needs_client_extraction",
            "source": source,
            "cached": False,
            "cached_batch_offsets": sorted(
                self.repo.batches(study_id, source["fingerprint"], source["reading"], "analysis")
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
                "progress survives context compaction (skip offsets already in cached_batch_offsets). Always "
                "continue at the next_offset the previous response returned, and cache the last batch with its "
                "batch_offset too; then call it once more with coverage_complete=true (analysis may be {}) to "
                "merge and save. A quote has at least 8 characters (quote the whole line around a bare code or "
                "word) and a fact at most 8 quotes (split the fact for more). A batch that was not read and cached is reported, not skipped. Later batches "
                "omit this instruction and analysis_schema."
                + (SINGLE_PASS_INSTRUCTION.format(question=question) if question else ""),
                analysis_schema=Extraction.model_json_schema(),
                **({"answer_schema": ProtocolAnswer.model_json_schema()} if question else {}),
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
            self.repo.clear_batches(study_id)
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

    async def research_protocol(
        self, protocol_id: str, question: str, force: bool = False, poll: bool = True
    ):
        """poll=False waits for a provider's answer however long it takes (see Explorer.research)."""
        return await self.explorer.research(protocol_id, question, force, poll)

    async def cache_protocol_answer(
        self, protocol_id: str, question: str, answer: ProtocolAnswer, reading: str | None = None
    ):
        """Cache a caller's answer under the reading research_protocol returned with the question."""
        source = {"protocol_id": protocol_id}
        translations = self._reading_snapshot(source, reading, "answer the question again")
        return await asyncio.to_thread(self.explorer.save, protocol_id, question, answer, None, translations)

    async def cache_protocol_analysis(
        self,
        study_id: str,
        fingerprint: str,
        analysis: Extraction,
        coverage_complete: bool = False,
        batch_offset: int | None = None,
        reading: str | None = None,
        answer: ProtocolAnswer | None = None,
        question: str | None = None,
    ):
        """Save a caller extraction. With batch_offset the batch is kept in the database until the final
        coverage_complete call merges every cached batch, so neither context compaction nor a server restart
        loses progress; the merge refuses to save while a batch was never read (spec v1.11).
        reading is analyze_protocol's source.reading: a non-English protocol's batches must have been read
        under its current heading translations. answer/question: what this batch states about the question
        given to analyze_protocol; the final call saves the merged answer for research_protocol to serve."""
        if not coverage_complete and batch_offset is None:
            raise RWEError(
                "INCOMPLETE_EXTRACTION",
                "Read all section batches before caching, or cache each batch with batch_offset first.",
            )
        if answer is not None and not (question or "").strip():
            raise RWEError("INVALID_INPUT", "answer needs the question it answers.")
        if question is not None and len(question) > 2000:
            # Refused with the batch that carries it, not only when the merged answer is saved at the end.
            raise RWEError("INVALID_INPUT", "Question must be 1..2000 characters.")
        pdf, source = await self._context(study_id)
        if source["fingerprint"] != fingerprint:
            raise RWEError("PROTOCOL_CHANGED", "Protocol/extractor changed; analyze current protocol again.")
        translations = self._reading_snapshot(source, reading, "reread the batches")
        pages = await asyncio.to_thread(extract_pages, pdf, True, translations)
        analysis = with_corrected_pages(analysis, pages)
        if answer is not None:
            answer = with_corrected_pages(answer, pages)
        analysis = analysis.model_copy(
            update={
                "data_sources": with_source_name_quotes(analysis.data_sources, pages),
                "source_assessments": with_source_name_quotes(analysis.source_assessments, pages),
            }
        )
        if answer is not None:
            answer = answer.model_copy(
                update={"source_assessments": with_source_name_quotes(answer.source_assessments, pages)}
            )
        validate_evidence(analysis, pages)
        key = (study_id, fingerprint, source["reading"])
        served = self.repo.batches(*key, "served")
        if batch_offset is not None and batch_offset not in served:
            raise RWEError(
                "INVALID_INPUT",
                f"No batch was read at offset {batch_offset}: batch_offset is the offset of an analyze_protocol "
                "response for this protocol.",
            )
        if answer is not None:
            for start in range(0, len(answer.answers), 20):  # Extraction.key_notes holds 20 facts per check
                validate_evidence(Extraction(key_notes=answer.answers[start : start + 20]), pages)
            validate_evidence(Extraction(source_assessments=answer.source_assessments), pages)
            # The final call's own answer is merged after every batch's (FINAL sorts past any section index).
            self.repo.save_batch(
                *key,
                answer_kind(question),
                FINAL if batch_offset is None else batch_offset,
                answer.model_dump(),
            )
        if batch_offset is not None:
            self.repo.save_batch(*key, "analysis", batch_offset, analysis.model_dump())
        if not coverage_complete:
            return {
                "status": "batch_cached",
                "study_id": study_id,
                "cached_batch_offsets": sorted(self.repo.batches(*key, "analysis")),
            }
        cached = self.repo.batches(*key, "analysis")
        if not cached and analysis == Extraction():
            # A repeated final call after the merge cleared the batches: nothing to merge, nothing given.
            before = self.repo.analysis(study_id)
            held = dict(before["analysis"], missing_information=[]) if before else None
            if held and held != Extraction().model_dump():
                raise RWEError(
                    "INCOMPLETE_EXTRACTION",
                    "No batch is cached and the analysis given is empty: the saved analysis is kept. It was "
                    "merged by an earlier coverage_complete call, which cleared the batches. To redo the "
                    "extraction, call analyze_protocol with force_refresh=true and cache every batch again.",
                )
        # Batches cached one by one must hold every section between them. A final call that carries the last
        # batch without its offset stands for the one batch that ends the protocol, never for a skipped one.
        total, position = len(reading_order(sections(pages))), 0
        while cached and position < total:
            step = served.get(position)
            if position not in cached and not (step and step["next"] >= total and batch_offset is None):
                raise RWEError(
                    "INCOMPLETE_EXTRACTION",
                    f"Sections from offset {position} were not read and cached: call analyze_protocol with "
                    f"offset={position}, cache that batch with batch_offset={position}, follow next_offset to "
                    "the end, then repeat the coverage_complete call.",
                )
            position = step["next"]
        partials = [Extraction.model_validate(b) for o, b in cached.items() if o != batch_offset]
        if partials:
            analysis = merge_extractions([*partials, analysis])
        answer_saved = None
        if question:
            answer_saved = await self._save_batch_answers(source, key, question, translations)
        notes = audit_extraction(analysis, pages, self.repo.get(study_id).catalogue_data_sources)
        if notes:
            analysis.missing_information = with_notes(analysis.missing_information, notes)
        saved = self._save(study_id, analysis, source, "client_assisted", detail="summary")
        self.repo.clear_batches(study_id)
        return saved if answer_saved is None else {**saved, "answer_saved": answer_saved}

    async def _save_batch_answers(self, source: dict, key: tuple, question: str, translations: dict) -> bool:
        """Merge the answers cached with the batches into the saved answer to the question; False when the
        batches hold nothing about it (the caller then explores with research_protocol)."""
        parts = [
            ProtocolAnswer.model_validate(body)
            for _, body in sorted(self.repo.batches(*key, answer_kind(question)).items())
        ]
        answers = [fact for part in parts for fact in part.answers]
        assessments = [fact for part in parts for fact in part.source_assessments]
        if not answers and not assessments:
            return False
        missing = [note for part in parts for note in part.missing_information]
        if len(answers) > 30:
            missing.insert(
                0, f"{len(answers)} verified answers found; only the first 30 are kept (schema limit)."
            )
        if len(assessments) > 120:
            missing.insert(0, f"{len(assessments)} source assessments found; only the first 120 were kept.")
        merged = ProtocolAnswer(
            answers=answers[:30],
            source_assessments=assessments[:120],
            missing_information=with_notes(missing, []),
        )
        await asyncio.to_thread(
            self.explorer.save,
            source["protocol_id"],
            question,
            merged,
            [{"mode": "single_pass"}],
            translations,
        )
        return True

    async def _provider_extraction(self, study_id: str, pdf: bytes, source: dict, on_progress=None) -> dict:
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
        deadline, reported = asyncio.get_running_loop().time() + self.settings.llm_wait_seconds, None
        while True:
            left = deadline - asyncio.get_running_loop().time()
            # With a listener, wake up every second to pass on how many batches are done.
            done, _ = await asyncio.wait({task}, timeout=max(0.0, min(left, 1.0) if on_progress else left))
            if task in done or left <= 0:
                break
            state = (progress.get("done"), progress.get("total"))
            if on_progress and state[1] and state != reported:
                reported = state
                await on_progress(state[0], state[1], f"Extracting: {state[0]} of {state[1]} batches done")
        if task in done:
            self._extractions.pop(key, None)
            if task.cancelled():
                raise RWEError(
                    "PROTOCOL_CHANGED",
                    "Protocol or its heading translations changed during extraction; call analyze_protocol again.",
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
        chunks = compact_spaces(reading_order(sections(pages)))
        if not chunks:
            raise RWEError("SECTION_PARSE_FAILED", "No relevant protocol sections found.")
        checkpoint = ProviderCheckpoint(self.repo, study_id, source["fingerprint"], source["reading"])
        analysis = await extract_with_provider(self.settings, chunks, progress, checkpoint)
        catalogue_sources = self.repo.get(study_id).catalogue_data_sources
        analysis, _ = await asyncio.to_thread(finalize_extraction, analysis, pages, catalogue_sources)
        validate_evidence(analysis, pages)
        saved = self._save(study_id, analysis, source, "configured_provider")
        self.repo.clear_batches(study_id)
        return saved

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


class ProviderCheckpoint:
    """The finished batches of a server-side extraction, kept in extraction_batches under the PDF, extractor
    and reading they were read with (the table drops batches of any other): a failed batch or a server
    restart costs only the batches that had not finished."""

    def __init__(self, repo, study_id: str, fingerprint: str, reading: str):
        self.repo, self.key = repo, (study_id, fingerprint, reading)

    def finished(self) -> dict[int, dict]:
        return self.repo.batches(*self.key, "provider")

    def save(self, index: int, sections: str, extraction: Extraction) -> None:
        self.repo.save_batch(
            *self.key, "provider", index, {"sections": sections, "analysis": extraction.model_dump()}
        )


FINAL = 10**9  # batch offset of the answer passed with the coverage_complete call itself

SINGLE_PASS_INSTRUCTION = (
    " ALSO ANSWER THIS QUESTION while reading (one pass; do not call research_protocol for it): {question!r}. "
    "With every batch pass `question` and `answer` to cache_protocol_analysis: an object matching answer_schema "
    "holding what THIS batch states about the question (answers=[] when nothing), under the evidence rules of "
    "the analysis. Give each item its own fact with the quote that states it (one criterion, definition or "
    "condition per fact; do not gather a list into one fact). The question never narrows the extraction: fill "
    "the analysis completely. The coverage_complete call (pass `question` again) merges the batch answers and "
    "saves them as the answer; only if it reports answer_saved=false, call research_protocol afterwards."
)


def answer_kind(question: str) -> str:
    return "answer:" + hashlib.sha256(question.encode()).hexdigest()[:16]


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
