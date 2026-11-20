"""Server-side (provider) extraction: batching, merging within schema limits, evidence pruning, polling."""

import asyncio
import json

import pytest
from test_core import sample_analysis

from ema_rwe.domain import Evidence, Extraction, Fact, ProtocolAnswer, RWEError, with_notes
from ema_rwe.llm import drop_invalid_evidence, extract_with_provider, split_batches
from ema_rwe.pdf import extract_pages, prune_unverifiable
from ema_rwe.service import Service


def test_split_batches_respects_limit_and_keeps_oversized_sections():
    chunks = [{"text": "a" * 40}, {"text": "b" * 40}, {"text": "c" * 200}, {"text": "d" * 10}]
    batches = split_batches(chunks, 100)
    assert [len(b) for b in batches] == [2, 1, 1]
    assert split_batches(chunks, 10_000) == [chunks]


def test_drop_invalid_evidence_removes_short_quotes_and_empty_facts():
    raw = {
        "outcomes": [
            {
                "value": "kept",
                "evidence": [{"page": 1, "quote": "None."}, {"page": 1, "quote": "long enough quote"}],
            },
            {"value": "dropped", "evidence": [{"page": 2, "quote": "short"}]},
        ],
        "population": {"value": "dropped too", "evidence": [{"page": 1, "quote": "tiny"}]},
        "missing_information": ["untouched"],
    }
    cleaned = drop_invalid_evidence(raw)
    assert [o["value"] for o in cleaned["outcomes"]] == ["kept"]
    assert len(cleaned["outcomes"][0]["evidence"]) == 1
    assert cleaned["population"] is None
    assert cleaned["missing_information"] == ["untouched"]


async def test_parallel_batches_merge_within_schema_limits(settings, monkeypatch):
    settings.llm_base_url = "https://provider.example/v1"
    settings.llm_model = "test-model"
    settings.llm_batch_chars = 50
    settings.llm_concurrency = 3
    seen = []

    async def fake_complete(_settings, messages):
        seen.append(messages[1]["content"])
        note = Fact(value="note", evidence=[Evidence(page=1, quote="a quote long enough")])
        return Extraction(
            key_notes=[note.model_copy(update={"value": f"b{len(seen)}n{i}"}) for i in range(15)]
        ).model_dump()

    monkeypatch.setattr("ema_rwe.llm.complete_json", fake_complete)
    progress = {}
    chunks = [{"page": 1, "text": "x" * 40} for _ in range(3)]
    result = await extract_with_provider(settings, chunks, progress)
    max_notes = Extraction.model_json_schema()["properties"]["key_notes"]["maxItems"]
    assert len(seen) == 3 and progress == {"done": 3, "total": 3, "reused": 0}
    assert len(result.key_notes) == max_notes
    assert all('"of": 3' in payload for payload in seen)
    # The facts beyond the limit are reported, not dropped silently
    assert result.missing_information == [
        (
            f"key_notes: 45 found across batches; only {max_notes} are kept (schema limit), "
            f"{45 - max_notes} omitted, taken in turn from the 3 batches."
        )
    ]


def test_missing_information_overflow_keeps_the_notes_and_counts_the_rest():
    missing = [f"model gap {i}" for i in range(30)]
    merged = with_notes(missing, ["audit note"])
    assert len(merged) == 30 and merged[-2:] == [
        "audit note",
        "2 further missing-information entries omitted (limit 30).",
    ]
    assert with_notes(["a"], ["b", "a"]) == ["a", "b"]


def test_prune_unverifiable_keeps_verifiable_evidence_only(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    analysis = sample_analysis()
    good = analysis.data_sources[0].evidence[0]
    analysis.outcomes = [
        Fact(value="mixed", evidence=[good, Evidence(page=1, quote="this sentence is not in the pdf")]),
        Fact(value="gone", evidence=[Evidence(page=1, quote="neither is this one at all")]),
    ]
    pruned, dropped = prune_unverifiable(analysis, pages)
    assert dropped == 2
    assert [o.value for o in pruned.outcomes] == ["mixed"]
    assert pruned.outcomes[0].evidence == [good]


async def test_analyze_protocol_returns_extracting_then_result(service, monkeypatch):
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    service.settings.llm_wait_seconds = 0.05
    release = asyncio.Event()

    async def slow_extract(_settings, _chunks, progress=None, checkpoint=None):
        await release.wait()
        return sample_analysis()

    monkeypatch.setattr("ema_rwe.extraction.extract_with_provider", slow_extract)
    study_id = "123"
    pending = await service.analyze_protocol(study_id)
    assert pending["status"] == "extracting" and "progress" in pending
    release.set()
    for _ in range(50):
        done = await service.analyze_protocol(study_id)
        if done["status"] == "analyzed":
            break
        await asyncio.sleep(0.02)
    assert done["status"] == "analyzed" and done["extraction_method"] == "configured_provider"
    assert (await service.analyze_protocol(study_id))["cached"] is True


async def test_protocol_changed_during_extraction_never_returns_the_old_result(
    service, website, pdf_bytes, monkeypatch
):
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    service.settings.llm_wait_seconds = 0.05
    releases = []

    async def slow_extract(_settings, _chunks, progress=None, checkpoint=None):
        releases.append(asyncio.Event())
        await releases[-1].wait()
        return sample_analysis()

    monkeypatch.setattr("ema_rwe.extraction.extract_with_provider", slow_extract)
    old = await service.analyze_protocol("123")
    assert old["status"] == "extracting"
    website["/system/files/protocol.pdf"] = pdf_bytes + b"\n%updated"
    new = await service.analyze_protocol("123", force_refresh=True)
    assert new["status"] == "extracting"
    assert new["source"]["fingerprint"] != old["source"]["fingerprint"]
    for release in releases:
        release.set()
    for _ in range(50):
        done = await service.analyze_protocol("123")
        if done["status"] == "analyzed":
            break
        await asyncio.sleep(0.02)
    # The result and the saved analysis describe the new PDF, not the one the first task was reading
    assert done["source"]["fingerprint"] == new["source"]["fingerprint"]
    assert service.repo.analysis("123")["source"]["fingerprint"] == new["source"]["fingerprint"]


@pytest.mark.parametrize("value", [0, -1])
def test_settings_concurrency_floor(settings, value):
    settings.llm_concurrency = value
    assert max(1, settings.llm_concurrency) == 1


async def test_batch_caching_merges_partials_on_completion(service):
    first = await service.analyze_protocol("123")
    assert first["status"] == "needs_client_extraction" and first["cached_batch_offsets"] == []
    fingerprint = first["source"]["fingerprint"]
    batch = sample_analysis()
    partial = Extraction(data_sources=batch.data_sources)
    held = await service.cache_protocol_analysis("123", fingerprint, partial, batch_offset=0)
    assert held["status"] == "batch_cached" and held["cached_batch_offsets"] == [0]
    assert (await service.analyze_protocol("123"))["cached_batch_offsets"] == [0]
    final = Extraction(study_design=batch.study_design)
    done = await service.cache_protocol_analysis("123", fingerprint, final, coverage_complete=True)
    assert done["status"] == "analyzed" and "analysis" not in done  # summary by default
    assert done["analysis_summary"]["data_sources"] == 1 and done["data_sources"][0]["usage"] == "planned"
    full = await service.analyze_protocol("123", detail="full")
    assert full["analysis"]["data_sources"][0]["value"] == batch.data_sources[0].value
    assert full["analysis"]["study_design"]["value"] == batch.study_design.value
    assert service.repo.batches("123", fingerprint, first["source"]["reading"], "analysis") == {}


async def test_incomplete_without_batch_offset_is_rejected(service):
    first = await service.analyze_protocol("123")
    with pytest.raises(RWEError, match="batch_offset"):
        await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis())


async def test_advertised_schemas_have_no_titles():
    from ema_rwe.mcp.server import create_server

    tools = await create_server().list_tools()
    dumped = json.dumps([t.inputSchema for t in tools])
    assert '"title"' not in dumped
    assert any(
        t.name == "cache_protocol_analysis" and "batch_offset" in t.inputSchema["properties"] for t in tools
    )


def test_prune_repairs_neighbour_page_and_source_name(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    analysis = sample_analysis()
    quote = analysis.data_sources[0].evidence[0].quote
    analysis.outcomes = [Fact(value="off by one", evidence=[Evidence(page=2, quote=quote)])]
    analysis.data_sources[0].value = "EPCD"  # not verbatim in the quote's normalised form -> window added
    pruned, dropped = prune_unverifiable(analysis, pages)
    assert dropped == 0
    assert pruned.outcomes[0].evidence[0].page == 1
    assert any("epcd" in e.quote.lower() for e in pruned.data_sources[0].evidence)


async def test_research_protocol_answers_from_full_text_in_one_pass(service, monkeypatch):
    from test_exploration import answer

    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend, service.settings.llm_model = "litellm", "test"
    calls = []

    async def respond(settings, messages):
        calls.append(json.loads(messages[1]["content"]))
        bad = {"value": "not in pdf", "evidence": [{"page": 1, "quote": "this quote is fabricated text"}]}
        return {**answer().model_dump(), "answers": answer().model_dump()["answers"] + [bad]}

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    result = await service.research_protocol(pid, "Which adjustment method?")
    assert result["status"] == "answered" and len(calls) == 1
    assert calls[0]["of"] == 1 and "sections" in calls[0] and "question" in calls[0]
    assert result["trace"][0]["mode"] == "provider_full_text" and result["trace"][0]["dropped"] == 1
    assert [a["value"] for a in result["answer"]["answers"]] == [answer().answers[0].value]
    assert any("dropped" in m for m in result["answer"]["missing_information"])
    assert (await service.research_protocol(pid, "Which adjustment method?"))["cached"]


def test_prune_drops_original_quote_when_only_the_window_verifies(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    analysis = sample_analysis()
    source = analysis.data_sources[0]
    # Wrong section label on the original quote and a value that is not inside the quote's text.
    source.evidence[0].section = "9.9 Wrong section"
    source.value = "EPCD"
    pruned, _ = prune_unverifiable(analysis, pages)
    kept = pruned.data_sources[0].evidence
    assert all(e.section != "9.9 Wrong section" for e in kept) and kept
    from ema_rwe.pdf import validate_evidence

    validate_evidence(pruned, pages)


def test_prune_fills_missing_section_label_from_the_quote_location(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    analysis = sample_analysis()
    for fact in (analysis.study_design, *analysis.data_sources, *analysis.disease_definitions):
        for evidence in fact.evidence:
            evidence.section = None
    pruned, dropped = prune_unverifiable(analysis, pages)
    assert dropped == 0
    assert pruned.study_design.evidence[0].section == "8.1 Study design"
    assert pruned.data_sources[0].evidence[0].section == "8.2 Data sources"


async def test_client_batches_carry_schema_only_once_and_allow_large_pages(service):
    first = await service.analyze_protocol("123", max_chars=150000)
    assert "analysis_schema" in first and "instruction" in first
    later = await service.analyze_protocol("123", offset=1, max_chars=6000)
    assert later["status"] == "needs_client_extraction"
    assert "analysis_schema" not in later and "instruction" not in later
    with pytest.raises(RWEError, match="max_chars"):
        await service.analyze_protocol("123", max_chars=200000)


async def test_search_and_research_respect_text_budgets(service):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    hits = await service.search_protocol_text(pid, "cohort", max_chars=1000)
    assert hits["max_chars"] == 1000 and hits["results"][0]["text"]
    bundle = await service.research_protocol(pid, "Which cohort design?")
    assert bundle["status"] == "needs_client_exploration"
    assert bundle["max_chars"] == service.settings.research_budget_chars
    assert bundle["results"] and "read them first" in bundle["instruction"]
    compact = await service.get_protocol_outline(pid)
    assert set(compact["sections"][0]) == {"section_id", "page", "section", "role"}
    full = await service.get_protocol_outline(pid, detail="full")
    assert "parent_section" in full["sections"][0]


async def test_cached_batches_survive_a_server_restart(service):
    first = await service.analyze_protocol("123")
    fingerprint = first["source"]["fingerprint"]
    partial = Extraction(data_sources=sample_analysis().data_sources)
    await service.cache_protocol_analysis("123", fingerprint, partial, batch_offset=0)
    restarted = Service(service.settings, client=service.client)
    assert (await restarted.analyze_protocol("123"))["cached_batch_offsets"] == [0]
    done = await restarted.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert done["status"] == "analyzed" and done["analysis_summary"]["data_sources"] == 1


async def test_a_batch_that_was_never_read_stops_the_merge(service):
    later = await service.analyze_protocol("123", offset=1, max_chars=6000)
    fingerprint = later["source"]["fingerprint"]
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), batch_offset=1)
    with pytest.raises(RWEError) as error:
        await service.cache_protocol_analysis("123", fingerprint, sample_analysis(), coverage_complete=True)
    assert error.value.code == "INCOMPLETE_EXTRACTION" and "offset=0" in error.value.message
    assert service.repo.analysis("123") is None
    # Reading the missing batch completes the extraction; the batches cached so far were kept.
    await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", fingerprint, sample_analysis(), batch_offset=0)
    done = await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert done["status"] == "analyzed"


async def test_a_batch_offset_that_was_not_served_is_rejected(service):
    first = await service.analyze_protocol("123")
    with pytest.raises(RWEError) as error:
        await service.cache_protocol_analysis(
            "123", first["source"]["fingerprint"], sample_analysis(), batch_offset=99
        )
    assert error.value.code == "INVALID_INPUT"
    assert (
        first["cached_batch_offsets"] == (await service.analyze_protocol("123"))["cached_batch_offsets"] == []
    )


async def test_question_is_answered_in_the_same_pass_as_the_extraction(service):
    question = "How is diabetes defined?"
    first = await service.analyze_protocol("123", question=question)
    assert question in first["instruction"] and "answers" in first["answer_schema"]["properties"]
    assert "answer_schema" not in await service.analyze_protocol("123")  # only when a question is given
    fingerprint = first["source"]["fingerprint"]
    answer = ProtocolAnswer(answers=sample_analysis().disease_definitions)
    with pytest.raises(RWEError) as error:  # an answer without its question cannot be saved
        await service.cache_protocol_analysis(
            "123", fingerprint, sample_analysis(), batch_offset=0, answer=answer
        )
    assert error.value.code == "INVALID_INPUT"
    await service.cache_protocol_analysis(
        "123", fingerprint, sample_analysis(), batch_offset=0, answer=answer, question=question
    )
    done = await service.cache_protocol_analysis(
        "123", fingerprint, Extraction(), coverage_complete=True, question=question
    )
    assert done["status"] == "analyzed" and done["answer_saved"] is True
    saved = await service.research_protocol(first["source"]["protocol_id"], question)
    assert saved["status"] == "answered" and saved["cached"] is True
    assert saved["answer"]["answers"][0]["value"] == answer.answers[0].value
    assert saved["trace"] == [{"mode": "single_pass"}]


async def test_single_pass_without_an_answer_leaves_the_question_to_research(service):
    question = "Which sensitivity analyses are planned?"
    first = await service.analyze_protocol("123", question=question)
    fingerprint = first["source"]["fingerprint"]
    await service.cache_protocol_analysis(
        "123", fingerprint, sample_analysis(), batch_offset=0, answer=ProtocolAnswer(), question=question
    )
    done = await service.cache_protocol_analysis(
        "123", fingerprint, Extraction(), coverage_complete=True, question=question
    )
    assert done["answer_saved"] is False
    assert (await service.research_protocol(first["source"]["protocol_id"], question))["status"] == (
        "needs_client_exploration"
    )


class MemoryCheckpoint:
    def __init__(self):
        self.batches = {}

    def finished(self):
        return dict(self.batches)

    def save(self, index, sections, extraction):
        self.batches[index] = {"sections": sections, "analysis": extraction.model_dump()}


def note(value: str) -> dict:
    return Extraction(
        key_notes=[Fact(value=value, evidence=[Evidence(page=1, quote="a quote long enough")])]
    ).model_dump()


async def test_finished_provider_batches_are_kept_and_only_the_rest_is_asked_again(settings, monkeypatch):
    settings.llm_base_url, settings.llm_model = "https://provider.example/v1", "test-model"
    settings.llm_batch_chars, settings.llm_concurrency = 50, 1
    chunks = [{"page": 1, "text": letter * 40} for letter in "abc"]
    asked, fail = [], {"b"}

    async def fake_complete(_settings, messages):
        letter = json.loads(messages[1]["content"])["sections"][0]["text"][0]
        asked.append(letter)
        if letter in fail:
            raise RWEError("LLM_EXTRACTION_FAILED", "Provider request or structured output failed.")
        return note(f"note {letter}")

    monkeypatch.setattr("ema_rwe.llm.complete_json", fake_complete)
    checkpoint, progress = MemoryCheckpoint(), {}
    with pytest.raises(RWEError) as error:
        await extract_with_provider(settings, chunks, progress, checkpoint)
    # The batch after the failed one still ran and was kept
    assert error.value.code == "LLM_EXTRACTION_FAILED" and asked == ["a", "b", "c"]
    assert sorted(checkpoint.batches) == [0, 2] and progress == {"done": 2, "total": 3, "reused": 0}
    asked.clear()
    fail.clear()
    progress = {}
    result = await extract_with_provider(settings, chunks, progress, checkpoint)
    assert asked == ["b"] and progress == {"done": 3, "total": 3, "reused": 2}
    assert [n.value for n in result.key_notes] == ["note a", "note b", "note c"]


async def test_a_saved_batch_of_other_sections_is_not_reused(settings, monkeypatch):
    settings.llm_base_url, settings.llm_model = "https://provider.example/v1", "test-model"
    settings.llm_batch_chars = 50
    asked = []

    async def fake_complete(_settings, messages):
        asked.append(json.loads(messages[1]["content"])["sections"][0]["text"][0])
        return note(f"note {asked[-1]}")

    monkeypatch.setattr("ema_rwe.llm.complete_json", fake_complete)
    checkpoint = MemoryCheckpoint()
    await extract_with_provider(
        settings, [{"page": 1, "text": "a" * 40}, {"page": 1, "text": "b" * 40}], {}, checkpoint
    )
    asked.clear()
    # Batch 0 holds the same text, batch 1 another one (the sections or the batch size changed)
    changed = [{"page": 1, "text": "a" * 40}, {"page": 1, "text": "z" * 40}]
    result = await extract_with_provider(settings, changed, {}, checkpoint)
    assert asked == ["z"] and [n.value for n in result.key_notes] == ["note a", "note z"]
    # A saved batch that no longer fits the schema is extracted again
    checkpoint.batches[0]["analysis"] = {"key_notes": [{"value": "", "evidence": []}]}
    asked.clear()
    await extract_with_provider(settings, changed, {}, checkpoint)
    assert asked == ["a"]


async def test_server_side_extraction_resumes_after_a_restart(service, monkeypatch):
    service.settings.llm_base_url, service.settings.llm_model = "https://provider.example/v1", "test-model"
    service.settings.llm_batch_chars, service.settings.llm_concurrency = 1, 1  # one section per batch
    design = sample_analysis().study_design
    calls, fail_after = [], [1]

    async def fake_complete(_settings, messages):
        calls.append(json.loads(messages[1]["content"])["batch"])
        if len(calls) > fail_after[0]:
            raise RWEError("LLM_EXTRACTION_FAILED", "Provider request or structured output failed.")
        return Extraction(study_design=design).model_dump() if calls[-1] == 1 else Extraction().model_dump()

    monkeypatch.setattr("ema_rwe.llm.complete_json", fake_complete)
    with pytest.raises(RWEError):
        await service.analyze_protocol("123")
    total = max(calls)
    assert total > 1 and service.repo.analysis("123") is None
    source = (await service.get_protocol("123"))["protocol"]
    with service.repo.connection() as db:
        kept = db.execute(
            "SELECT batch_offset FROM extraction_batches WHERE study_id='123' AND kind='provider'"
        ).fetchall()
    assert [row[0] for row in kept] == [0] and source["protocol_id"]
    # A new server process: the task registry is gone, the finished batch is not
    restarted = Service(service.settings, client=service.client)
    calls.clear()
    fail_after[0] = 10**6
    done = await restarted.analyze_protocol("123", detail="full")
    assert done["status"] == "analyzed" and 1 not in calls and len(calls) == total - 1
    assert done["analysis"]["study_design"]["value"] == design.value
    with restarted.repo.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM extraction_batches WHERE study_id='123'").fetchone()[0] == 0


def test_entries_repeated_across_batches_merge_and_an_overfull_list_keeps_every_batch():
    """spec v1.23. Two readers of the reference run met the 60-source limit with the same sources named in
    several batches, and lost the notes of the later batches to the 20-note limit."""
    from ema_rwe.domain import DataSource, Evidence, Fact, SourceAssessment
    from ema_rwe.llm import merge_extractions

    def quote(text, page=1):
        return [Evidence(page=page, quote=f"The protocol says {text} here.")]

    def assessment(basis, linkage, definition, text):
        return SourceAssessment(
            value="Danish Cancer Registry", usage="planned", types=["registry"], role="outcome",
            basis=basis, definition=definition, requires_linkage=linkage, evidence=quote(text),
        )  # fmt: skip

    first = Extraction(
        data_sources=[DataSource(value="Danish Cancer Registry", usage="candidate", evidence=quote("one"))],
        source_assessments=[assessment("inferred", False, "cancer diagnoses", "one")],
        key_notes=[Fact(value=f"design note {i}", evidence=quote(f"d{i}")) for i in range(18)],
    )
    second = Extraction(
        data_sources=[
            DataSource(value="danish cancer  registry.", usage="planned", evidence=quote("two", 2)),
            DataSource(value="Danish National Patient Register", usage="planned", evidence=quote("three")),
        ],
        source_assessments=[
            assessment("explicit", True, "linked by personal number", "two"),
            SourceAssessment(
                value="Danish Cancer Registry",
                usage="planned",
                types=["registry"],
                role="cohort",
                basis="explicit",
                definition="history of cancer",
                requires_linkage=False,
                evidence=quote("four"),
            ),
        ],
        key_notes=[Fact(value=f"analysis note {i}", evidence=quote(f"a{i}")) for i in range(18)],
    )
    merged = merge_extractions([first, second])
    # One source named twice is one entry with both quotes and the more definite usage
    assert [(d.value, d.usage, len(d.evidence)) for d in merged.data_sources] == [
        ("Danish Cancer Registry", "planned", 2),
        ("Danish National Patient Register", "planned", 1),
    ]
    # The same source in the same role is one assessment; another role stays its own
    outcome, cohort = merged.source_assessments
    assert (outcome.role, outcome.basis, outcome.requires_linkage) == ("outcome", "explicit", True)
    assert outcome.definition == "cancer diagnoses\nlinked by personal number" and cohort.role == "cohort"
    # 36 notes for 20 places: ten from each batch, not the first batch's eighteen and two
    kept = [note.value for note in merged.key_notes]
    assert len(kept) == 20 and sum(v.startswith("analysis") for v in kept) == 10
    assert kept[:10] == [f"design note {i}" for i in range(10)]
    assert merged.missing_information == [
        (
            "key_notes: 36 found across batches; only 20 are kept (schema limit), 16 omitted, "
            "taken in turn from the 2 batches."
        )
    ]
    # A source both planned and used stays planned
    used = Extraction(
        data_sources=[DataSource(value="Danish Cancer Registry", usage="used", evidence=quote("u"))]
    )
    assert merge_extractions([second, used]).data_sources[0].usage == "planned"


def test_answers_found_batch_by_batch_keep_the_last_batches():
    """spec v1.25: four readers of the reference run found 34 to 57 answers over their batches; the first
    30 were kept, and the code lists of the appendices, read last, were what was dropped."""
    from ema_rwe.domain import Evidence, Fact, ProtocolAnswer
    from ema_rwe.llm import ANSWER_LIMIT, merge_answers

    def answer(text, page=1):
        return Fact(value=text, evidence=[Evidence(page=page, quote=f"The protocol says {text} here.")])

    assert ANSWER_LIMIT == 60 == ProtocolAnswer.model_json_schema()["properties"]["answers"]["maxItems"]
    methods = [answer(f"definition {i}") for i in range(40)]
    appendix = [answer(f"code {i}", 90) for i in range(8)] + [answer("Definition 0.", 91)]
    merged, note = merge_answers([methods, appendix])
    # 48 answers after the repeated one is folded into its first statement: all kept
    assert note is None and len(merged) == 48 and [e.page for e in merged[0].evidence] == [1, 91]
    assert {fact.value for fact in merged} >= {f"code {i}" for i in range(8)}
    # Over the limit, both batches give in turn: the appendix is not what is left out
    long, note = merge_answers([[answer(f"definition {i}") for i in range(70)], appendix[:8]])
    assert len(long) == 60 and sum(fact.value.startswith("code") for fact in long) == 8
    assert note == (
        "78 verified answers found; only 60 are kept (schema limit), 18 omitted, "
        "taken in turn from the 2 batches."
    )
    one, note = merge_answers([[answer(f"definition {i}") for i in range(61)]])
    assert len(one) == 60 and note.endswith("1 omitted from the end.")


def four_section_pdf() -> bytes:
    """Four sections of about 5,000 characters: max_chars=6000 serves one per batch, 150000 all of them."""
    import pymupdf

    with pymupdf.open() as doc:
        for number, title in enumerate(
            ["Study design", "Data sources", "Disease definitions", "Analysis"], 1
        ):
            body = " ".join(f"The cohort study word{j} follows patients." for j in range(120))
            doc.new_page().insert_textbox(
                pymupdf.Rect(50, 50, 550, 800), f"8.{number} {title}\n{body}", fontsize=6
            )
        return doc.tobytes()


async def test_a_batch_served_again_with_another_budget_keeps_the_sections_it_held(service, website):
    """Review of PR #81: the merge followed the last served next_offset, not what the cached batch held."""
    website["/system/files/protocol.pdf"] = four_section_pdf()
    small = await service.analyze_protocol("123", max_chars=6000)
    fingerprint = small["source"]["fingerprint"]
    assert small["next_offset"] < small["total_sections"]
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), batch_offset=0)
    whole = await service.analyze_protocol("123", max_chars=150000)
    assert whole["next_offset"] is None
    with pytest.raises(RWEError) as error:
        await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert error.value.code == "INCOMPLETE_EXTRACTION"
    assert f"offset={small['next_offset']}" in error.value.message
    # Cached after the whole read, the batch holds every section, whatever is served at offset 0 later
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), batch_offset=0)
    await service.analyze_protocol("123", max_chars=6000)
    done = await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert done["status"] == "analyzed"


async def test_a_final_call_with_no_batch_cached_is_refused_while_the_protocol_has_unread_batches(
    service, website
):
    """The coverage check also holds when no batch at all was cached (found while releasing v0.5.8)."""
    website["/system/files/protocol.pdf"] = four_section_pdf()
    first = await service.analyze_protocol("123", max_chars=6000)
    assert first["next_offset"] < first["total_sections"]
    with pytest.raises(RWEError) as error:
        await service.cache_protocol_analysis(
            "123", first["source"]["fingerprint"], Extraction(), coverage_complete=True
        )
    assert error.value.code == "INCOMPLETE_EXTRACTION" and "offset=0" in error.value.message
    assert service.repo.analysis("123") is None


async def test_a_final_call_without_offset_stands_only_for_the_batch_that_ends_the_protocol(service, website):
    website["/system/files/protocol.pdf"] = four_section_pdf()
    offset, served = 0, []
    while offset is not None:
        batch = await service.analyze_protocol("123", offset=offset, max_chars=6000)
        served.append(offset)
        offset = batch["next_offset"]
    fingerprint = batch["source"]["fingerprint"]
    assert len(served) > 3
    for cached in (served[0], *served[2:-1]):
        await service.cache_protocol_analysis("123", fingerprint, Extraction(), batch_offset=cached)
    with pytest.raises(RWEError) as error:
        await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert error.value.code == "INCOMPLETE_EXTRACTION" and f"offset={served[1]}" in error.value.message
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), batch_offset=served[1])
    done = await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    assert done["status"] == "analyzed"


async def test_batch_answers_are_not_dropped_by_a_final_call_without_their_question(service):
    """Review of PR #81: the final call without the question cleared the cached answers silently."""
    question = "How is diabetes defined?"
    first = await service.analyze_protocol("123", question=question)
    fingerprint = first["source"]["fingerprint"]
    answer = ProtocolAnswer(answers=sample_analysis().disease_definitions)
    await service.cache_protocol_analysis(
        "123", fingerprint, sample_analysis(), batch_offset=0, answer=answer, question=question
    )
    for other in (None, question + " "):
        with pytest.raises(RWEError) as error:
            await service.cache_protocol_analysis(
                "123", fingerprint, Extraction(), coverage_complete=True, question=other
            )
        assert error.value.code == "INVALID_INPUT" and "question" in error.value.message
    assert service.repo.analysis("123") is None
    done = await service.cache_protocol_analysis(
        "123", fingerprint, Extraction(), coverage_complete=True, question=question
    )
    assert done["status"] == "analyzed" and done["answer_saved"] is True


async def test_an_analysis_that_cannot_be_saved_leaves_no_answer_behind(service, monkeypatch):
    question = "How is diabetes defined?"
    first = await service.analyze_protocol("123", question=question)
    fingerprint = first["source"]["fingerprint"]
    answer = ProtocolAnswer(answers=sample_analysis().disease_definitions)
    await service.cache_protocol_analysis(
        "123", fingerprint, sample_analysis(), batch_offset=0, answer=answer, question=question
    )

    def refuse(*_args, **_kwargs):
        raise RWEError("READING_CONTEXT_CHANGED", "Heading translations changed.")

    monkeypatch.setattr(service.repo, "save_analysis", refuse)
    with pytest.raises(RWEError):
        await service.cache_protocol_analysis(
            "123", fingerprint, Extraction(), coverage_complete=True, question=question
        )
    research = await service.research_protocol(first["source"]["protocol_id"], question)
    assert research["status"] == "needs_client_exploration"


async def test_force_refresh_asks_the_provider_again_for_saved_batches(service, monkeypatch):
    """Review of PR #86: batches kept after a failed finish were reused even with force_refresh."""
    service.settings.llm_base_url, service.settings.llm_model = "https://provider.example/v1", "test-model"
    calls = []

    async def fake_complete(_settings, _messages):
        calls.append(1)
        return Extraction().model_dump()

    def refuse(*_args):
        raise RWEError("EVIDENCE_INVALID", "Evidence check failed.")

    monkeypatch.setattr("ema_rwe.llm.complete_json", fake_complete)
    monkeypatch.setattr("ema_rwe.extraction.validate_evidence", refuse)
    with pytest.raises(RWEError):
        await service.analyze_protocol("123")
    asked = len(calls)
    with pytest.raises(RWEError):
        await service.analyze_protocol("123")
    assert asked and len(calls) == asked
    with pytest.raises(RWEError):
        await service.analyze_protocol("123", force_refresh=True)
    assert len(calls) == 2 * asked


async def test_a_final_call_with_only_notes_after_the_merge_keeps_the_saved_analysis(service):
    """Review of PR #95: an analysis holding only missing_information is as empty as Extraction()."""
    first = await service.analyze_protocol("123")
    fingerprint = first["source"]["fingerprint"]
    await service.cache_protocol_analysis("123", fingerprint, sample_analysis(), batch_offset=0)
    await service.cache_protocol_analysis("123", fingerprint, Extraction(), coverage_complete=True)
    with pytest.raises(RWEError) as error:
        await service.cache_protocol_analysis(
            "123", fingerprint, Extraction(missing_information=["nothing new"]), coverage_complete=True
        )
    assert error.value.code == "INCOMPLETE_EXTRACTION"
    assert service.repo.analysis("123")["analysis"]["study_design"]


def test_merge_records_the_batch_of_every_list_entry_of_a_new_nested_block():
    """origins names the batch of each entry, in design_schema too, when cohort first arrives (R5 #96-4)."""
    from ema_rwe.llm import merge_into

    window = Fact(value="365 days", evidence=[Evidence(page=1, quote="A 365-day baseline period.")])
    output = Extraction.model_validate({"cohort": {"design_schema": {"time_windows": [window.model_dump()]}}})
    merged, origins = Extraction(), {}
    merge_into(merged, output, 3, origins)
    (kept,) = merged.cohort.design_schema.time_windows
    assert kept == window and origins[id(kept)] == 3


async def test_poll_false_waits_for_a_server_extraction_that_outlasts_the_wait(service, monkeypatch):
    """The CLI ends with its process, so a returned status=extracting would stop the extraction (spec v1.26)."""
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    service.settings.llm_wait_seconds = 0.02

    async def slow_extract(_settings, _chunks, progress=None, checkpoint=None):
        await asyncio.sleep(0.2)
        return sample_analysis()

    monkeypatch.setattr("ema_rwe.extraction.extract_with_provider", slow_extract)
    done = await service.analyze_protocol("123", poll=False)
    assert done["status"] == "analyzed" and done["extraction_method"] == "configured_provider"


async def test_cli_analyze_waits_for_the_extraction(tmp_path, monkeypatch):
    from ema_rwe.cli import parser, run

    monkeypatch.setenv("EMA_DB_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("EMA_CACHE_DIR", str(tmp_path / "http"))
    calls = []

    async def analyze(self, **kwargs):
        calls.append(kwargs)
        return {"status": "analyzed"}

    monkeypatch.setattr(Service, "analyze_protocol", analyze)
    await run(parser().parse_args(["analyze", "123"]))
    assert calls[0]["poll"] is False
