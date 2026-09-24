"""Server-side (provider) extraction: batching, merging within schema limits, evidence pruning, polling."""

import asyncio
import json

import pytest
from test_core import sample_analysis

from ema_rwe.domain import Evidence, Extraction, Fact, RWEError
from ema_rwe.llm import drop_invalid_evidence, extract_with_provider, split_batches
from ema_rwe.pdf import extract_pages, prune_unverifiable


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
    assert len(seen) == 3 and progress == {"done": 3, "total": 3}
    assert len(result.key_notes) == max_notes
    assert all('"of": 3' in payload for payload in seen)


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

    async def slow_extract(_settings, _chunks, progress=None):
        await release.wait()
        return sample_analysis()

    monkeypatch.setattr("ema_rwe.service.extract_with_provider", slow_extract)
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
    assert done["status"] == "analyzed"
    assert done["analysis"]["data_sources"][0]["value"] == batch.data_sources[0].value
    assert done["analysis"]["study_design"]["value"] == batch.study_design.value
    assert service._partials == {}


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
