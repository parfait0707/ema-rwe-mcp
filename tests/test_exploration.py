import json
import sys
from types import SimpleNamespace

import pytest

from ema_rwe.domain import Extraction, Fact, ProtocolAnswer, RWEError
from ema_rwe.llm import complete_json
from ema_rwe.pdf import Page, search_sections, sections, validate_evidence
from ema_rwe.service import Service
from ema_rwe.storage import import_csv
from ema_rwe.vocabulary import expand


def answer():
    return ProtocolAnswer.model_validate(
        {
            "answers": [
                {
                    "value": "Propensity score weighting and Cox regression",
                    "evidence": [
                        {
                            "page": 1,
                            "section": "8.4 Analysis",
                            "quote": "We will use propensity score weighting and Cox regression.",
                        }
                    ],
                }
            ]
        }
    )


def test_synonyms_find_spelling_variants(settings, csv_file):
    service = Service(settings)
    import_csv(service.repo, csv_file)
    study = service.repo.get("123")
    study.title = "Haemorrhage during anticoagulation"
    study.description = "Incident users"
    service.repo.upsert(study)
    result = service.search_studies("出血")
    assert result["results"][0]["study_id"] == "123"
    assert "haemorrhage" in result["query_expansion"]["synonyms"]
    assert service.search_studies("new-user")["results"]
    assert "atrial_fibrillation" not in expand("AF")["concepts"]


def test_context_excludes_references_and_contents_but_exploration_can_read_them():
    chunks = sections(
        [
            Page(1, "Contents\n8. Research methods .......... 2\n9. References .......... 3"),
            Page(
                2,
                "8. RESEARCH METHODS\n8.1 Missing data\nMultiple imputation will be used.\n"
                "8.2 Eligible participants\nAdults aged 18 years and above.",
            ),
            Page(3, "9. REFERENCES\n9.1 Missing data\nPrior authors used complete case analysis."),
        ]
    )
    assert chunks[0]["role"] == "contents" and not chunks[0]["relevant"]
    live = next(c for c in chunks if "Multiple imputation" in c["text"])
    assert live["parent_section"] == "8. RESEARCH METHODS" and live["relevant"]
    reference = next(c for c in chunks if "Prior authors" in c["text"])
    assert reference["role"] == "references" and not reference["relevant"]
    found = search_sections(chunks, "欠測値処理")
    assert found["results"][0]["page"] == 2
    assert any(c["page"] == 3 for c in found["results"])


def test_section_order_regression_is_visible():
    chunks = sections([Page(1, "8. RESEARCH METHODS\n8.5 Analysis\nCox model.\n8.2 Population\nAdults.")])
    assert chunks[-1]["structure_warnings"]


def test_checklist_questions_remain_inside_appendix_across_pages():
    chunks = sections(
        [
            Page(38, "APPENDIX II: Feasibility counts\nEight databases."),
            Page(39, "APPENDIX III: ENCePP checklist\n1.1 Milestones\nDoes the protocol describe methods?"),
            Page(43, "10.1 Analysis plan\nHow are missing data handled?\nmissing data?"),
        ]
    )
    assert chunks[-1]["role"] == "checklist"
    assert chunks[-1]["section"] == "APPENDIX III: ENCePP checklist"
    assert not chunks[-1]["relevant"]
    assert search_sections(chunks, "欠測値処理")["results"][0]["role"] == "checklist"


def test_authentic_reference_quote_cannot_be_cached_as_current_study_method():
    pages = [Page(1, "9. REFERENCES\nPrior study used propensity score weighting.")]
    fact = Fact(
        value="Propensity score weighting",
        evidence=[{"page": 1, "quote": "Prior study used propensity score weighting."}],
    )
    with pytest.raises(RWEError) as error:
        validate_evidence(Extraction(statistical_analysis=fact), pages)
    assert error.value.code == "EVIDENCE_WRONG_SECTION"
    validate_evidence(Extraction(key_notes=[fact]), pages)


def test_multiword_synonyms_do_not_degenerate_to_generic_words(settings, csv_file):
    service = Service(settings)
    import_csv(service.repo, csv_file)
    assert service.search_studies("DOAC")["results"] == []


async def test_local_archive_survives_restart_and_http_cache_cleanup(service):
    result = await service.get_protocol("123")
    pid = result["protocol"]["protocol_id"]
    assert result["protocol"]["local_filename"] == pid + ".pdf"
    for path in service.settings.cache_dir.glob("*"):
        path.unlink()
    restarted = Service(service.settings)
    try:
        hits = await restarted.search_protocol_text(pid, "傾向スコア")
        assert hits["network_requests"] == 0 and hits["results"]
        outline = await restarted.get_protocol_outline(pid, limit=1)
        assert outline["next_offset"] == 1
        read = await restarted.read_protocol_text(pid, section_id=hits["results"][0]["section_id"])
        assert "propensity score" in read["text"]
        assert (await restarted.list_local_protocols("123"))["protocols"][0]["protocol_id"] == pid
    finally:
        await restarted.close()


async def test_new_pdf_does_not_overwrite_old_version(service, website, pdf_bytes):
    first = await service.get_protocol("123")
    website["/system/files/protocol.pdf"] = pdf_bytes + b"\n%revision2"
    second = await service.get_protocol("123", refresh=True)
    a, b = first["protocol"]["protocol_id"], second["protocol"]["protocol_id"]
    assert a != b
    assert len((await service.list_local_protocols("123"))["protocols"]) == 2
    assert (await service.read_protocol_text(a))["source"]["protocol_id"] == a


@pytest.mark.parametrize("pid", ["../foo", "pdf_1_../foo", "E:/secret.pdf"])
async def test_pdf_ids_do_not_allow_arbitrary_paths(service, pid):
    with pytest.raises(RWEError):
        await service.read_protocol_text(pid)


async def test_followup_answer_persisted_separately_and_reused(service):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    n = len(service.requests)
    context = await service.research_protocol(pid, "Which adjustment method is used?")
    assert context["status"] == "needs_client_exploration"
    saved = await service.cache_protocol_answer(pid, "Which adjustment method is used?", answer())
    assert saved["status"] == "answered"
    again = await service.research_protocol(pid, "Which adjustment method is used?")
    assert again["cached"]
    assert service.repo.analysis("123") is None
    assert len(service.requests) == n
    forged = answer()
    forged.answers[0].evidence[0].quote = "Fabricated quotation that is not in this PDF."
    with pytest.raises(RWEError):
        await service.cache_protocol_answer(pid, "new question", forged)


async def test_autonomous_search_read_finish_and_cached_replay(service, monkeypatch):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend = "litellm"
    service.settings.llm_model = "anthropic/test"
    decisions = iter(
        [
            {"action": "search", "query": "adjustment", "synonyms": ["propensity score"]},
            {"action": "read", "start_page": 1},
            {"action": "finish", "answer": answer().model_dump()},
        ]
    )

    async def respond(settings, messages):
        return next(decisions)

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    count = len(service.requests)
    result = await service.research_protocol(pid, "Which adjustment method?")
    assert result["status"] == "answered"
    assert [x["action"]["action"] for x in result["trace"]] == ["search", "read"]
    assert (await service.research_protocol(pid, "Which adjustment method?"))["cached"]
    assert len(service.requests) == count


async def test_autonomous_loop_is_bounded_and_does_not_cache_unfinished_answer(service, monkeypatch):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend, service.settings.llm_model, service.settings.llm_max_steps = (
        "litellm",
        "test",
        2,
    )

    async def respond(settings, messages):
        return {"action": "shell", "command": "do not execute"}

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    result = await service.research_protocol(pid, "missingness")
    assert result["status"] == "exploration_limit_reached" and len(result["trace"]) == 2
    with service.repo.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM protocol_answers").fetchone()[0] == 0


@pytest.mark.parametrize(
    "model", ["gemini/test", "anthropic/test", "openai/test", "azure/deployment", "bedrock/test"]
)
async def test_litellm_provider_routing(settings, monkeypatch, model):
    settings.llm_backend, settings.llm_model = "litellm", model
    settings.llm_api_key = "test-only-key"

    async def completion(**kwargs):
        assert kwargs["model"] == model
        assert kwargs["api_key"] == "test-only-key"
        assert kwargs["num_retries"] == 0
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=completion))
    assert await complete_json(settings, [{"role": "user", "content": "Return JSON"}]) == {"ok": True}


async def test_llm_planner_validates_queries(service, monkeypatch):
    async def completion(settings, messages):
        return {"queries": ["bleeding cohort", "haemorrhage incident users"], "synonyms": ["hemorrhage"]}

    monkeypatch.setattr("ema_rwe.service.complete_json", completion)
    plan = await service.plan_study_search("出血", use_llm=True)
    assert len(plan["queries"]) == 2
    assert "hemorrhage" in plan["query_expansion"]["synonyms"]


async def test_actual_litellm_sdk_mock_completion(settings, monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    litellm = pytest.importorskip("litellm")
    original = litellm.acompletion

    async def mocked(**kwargs):
        return await original(**kwargs, mock_response=json.dumps({"ok": True}))

    monkeypatch.setattr(litellm, "acompletion", mocked)
    settings.llm_backend, settings.llm_model = "litellm", "openai/gpt-4o"
    assert await complete_json(settings, [{"role": "user", "content": "Return JSON"}]) == {"ok": True}
