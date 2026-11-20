import asyncio
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
    # Clinical translation comes from the caller; the server adds no disease synonyms of its own.
    assert service.search_studies("出血")["total_matches"] == 0
    result = service.search_studies("bleeding", synonyms=["haemorrhage"], darwin_only=False)
    assert result["results"][0]["study_id"] == "123"
    assert result["results"][0]["matched_term_sources"] == {"haemorrhage": "caller"}
    assert service.search_studies("new-user")["results"]
    af = expand("AF")
    assert "atrial_fibrillation" not in af["concepts"] + af["clinical"]["concepts"]


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
            Page(
                39,
                "APPENDIX III: ENCePP checklist\nSection 1: Milestones Yes No N/A Section Number\n"
                "1.1 Does the protocol specify timelines for start of data collection?\n"
                "1.2 Does the protocol describe methods?\n2.1 Is the study design described?",
            ),
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
    expansion = expand("IPTW")
    assert "inverse probability of treatment weighting" in expansion["synonyms"]
    # "of", "treatment" and "probability" appear in unrelated text; the phrase must co-occur.
    assert service.search_studies("IPTW")["results"] == []


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
            {"answers": [], "source_assessments": []},  # full-text pass finds nothing -> bounded loop
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
    # The caller continuing by hand caches under the reading the exploration started with
    assert result["reading"] == result["source"]["reading"]
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

    monkeypatch.setattr("ema_rwe.screening.complete_json", completion)
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


@pytest.mark.parametrize(
    "query,concept,synonym",
    [
        ("交絡の調整方法", "confounding", "confounding"),
        ("インデックス日の定義", "index_date", "index date"),
        ("症例対照研究", "case_control", "nested case control"),
        ("小児の研究", "paediatric", "pediatric"),
        ("データリンケージ", "linkage", "record linkage"),
    ],
)
def test_generic_protocol_vocabulary_translates_japanese_method_terms(query, concept, synonym):
    expansion = expand(query)
    assert concept in expansion["concepts"]
    assert synonym in expansion["synonyms"]


def test_specific_drug_classes_are_left_to_dictionaries_not_synonym_groups():
    for query in ("DOAC", "抗凝固薬", "オピオイド"):
        assert expand(query)["concepts"] == []


async def test_cli_saves_a_question_specific_answer(tmp_path, monkeypatch, pdf_bytes):
    """The CLI can answer needs_client_exploration as MCP callers do (cache-answer)."""
    from ema_rwe.archive import ProtocolArchive
    from ema_rwe.cli import parser, run

    monkeypatch.setenv("EMA_DB_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("EMA_CACHE_DIR", str(tmp_path / "http"))
    monkeypatch.setenv("EMA_PROTOCOL_DIR", str(tmp_path / "protocols"))
    pid = ProtocolArchive(tmp_path / "protocols").save("123", pdf_bytes, {})["protocol_id"]
    (tmp_path / "answer.json").write_text(answer().model_dump_json(), encoding="utf-8")
    saved = await run(
        parser().parse_args(["cache-answer", pid, "Adjustment?", str(tmp_path / "answer.json")])
    )
    assert saved["status"] == "answered"


async def test_provider_answer_that_outlasts_the_wait_is_collected_by_a_later_call(service, monkeypatch):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend, service.settings.llm_model = "litellm", "anthropic/test"
    service.settings.llm_wait_seconds = 0.02
    release, asked = asyncio.Event(), []

    async def respond(settings, messages):
        asked.append(1)
        await release.wait()
        return answer().model_dump()

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    question = "Which adjustment method is planned?"
    first = await service.research_protocol(pid, question)
    assert first["status"] == "answering" and first["question"] == question and "reading" in first
    # A second call joins the running exploration instead of starting another one, also with force
    assert (await service.research_protocol(pid, question))["status"] == "answering"
    assert (await service.research_protocol(pid, question, force=True))["status"] == "answering"
    assert len(asked) == 1
    release.set()
    for _ in range(50):
        result = await service.research_protocol(pid, question)
        if result["status"] == "answered":
            break
        await asyncio.sleep(0.02)
    assert (
        result["status"] == "answered"
        and result["answer"]["answers"][0]["value"] == answer().answers[0].value
    )
    assert (await service.research_protocol(pid, question))["cached"] and len(asked) == 1
    assert not service.explorer._running


async def test_cli_style_call_waits_for_the_provider_and_a_failure_starts_afresh(service, monkeypatch):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend, service.settings.llm_model = "litellm", "anthropic/test"
    service.settings.llm_wait_seconds = 0.01
    fail = [True]

    async def respond(settings, messages):
        await asyncio.sleep(0.05)  # longer than the polling wait
        if fail[0]:
            raise RWEError("LLM_EXTRACTION_FAILED", "Provider request or structured output failed.")
        return answer().model_dump()

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    question = "Which adjustment method is planned?"
    with pytest.raises(RWEError) as error:  # poll=False: the error of the run itself, not status=answering
        await service.research_protocol(pid, question, poll=False)
    assert error.value.code == "LLM_EXTRACTION_FAILED" and not service.explorer._running
    fail[0] = False
    done = await service.research_protocol(pid, question, poll=False)
    assert done["status"] == "answered"


def section_chunks(*rows):
    return [
        {
            "section_id": f"s{i:04}",
            "page": page,
            "section": section,
            "text": text,
            "role": role,
            "chapter": None,
        }
        for i, (page, section, text, role) in enumerate(rows, 1)
    ]


def raised(chunks):
    return {r["section"] for r in search_sections(chunks, "outcome", 30)["results"] if r.get("beside")}


def test_a_section_short_only_on_its_matching_page_does_not_raise_its_neighbours():
    """spec v1.23: the length of the section named for the question counts all its pages."""
    exposure = (
        5,
        "9.3.1 Exposure definition",
        "Exposure codes; the outcome uses the same window.",
        "methods",
    )
    outcomes = (6, "9.3.2 Outcomes", "The outcome is listed here.", "methods")
    assert raised(section_chunks(exposure, outcomes)) == {"9.3.1 Exposure definition"}
    more = [(page, "9.3.2 Outcomes", "Further rules of the section. " * 30, "methods") for page in (7, 8)]
    assert raised(section_chunks(exposure, outcomes, *more)) == set()


def test_only_sibling_sections_of_a_method_role_are_raised():
    chunks = section_chunks(
        (4, "9.3 Variables", "All variables, the outcome among them.", "methods"),
        (5, "9.3.1 Prior studies", "Earlier studies of the outcome.", "background"),
        (6, "9.3.2 Outcomes", "The outcome is listed here.", "methods"),
        (7, "9.3.2.1 Codes", "Codes of the outcome.", "methods"),
        (8, "9.3.3 Exposure definition", "Exposure codes; the outcome uses the same window.", "methods"),
    )
    assert raised(chunks) == {"9.3.3 Exposure definition"}


async def provider_waiting_for(service, monkeypatch, release, asked):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend, service.settings.llm_model = "litellm", "anthropic/test"
    service.settings.llm_wait_seconds = 0.02

    async def respond(settings, messages):
        asked.append(1)
        await release.wait()
        return answer().model_dump()

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    return pid


async def test_a_forced_call_after_the_exploration_ended_collects_its_answer(service, monkeypatch):
    """spec v1.15 3 and 5: calling again with the same arguments (force=true) receives the answer."""
    release, asked = asyncio.Event(), []
    pid = await provider_waiting_for(service, monkeypatch, release, asked)
    question = "Which adjustment method is planned?"
    assert (await service.research_protocol(pid, question, force=True))["status"] == "answering"
    release.set()
    await asyncio.gather(*service.explorer._running.values())
    calls = len(asked)
    result = await service.research_protocol(pid, question, force=True)
    assert result["status"] == "answered" and len(asked) == calls
    assert not service.explorer._running


async def test_a_call_during_a_forced_exploration_joins_it_instead_of_the_old_answer(service, monkeypatch):
    release, asked = asyncio.Event(), []
    pid = await provider_waiting_for(service, monkeypatch, release, asked)
    question = "Which adjustment method is planned?"
    release.set()
    assert (await service.research_protocol(pid, question, poll=False))["status"] == "answered"
    release.clear()
    assert (await service.research_protocol(pid, question, force=True))["status"] == "answering"
    joined = await service.research_protocol(pid, question)
    assert joined["status"] == "answering" and not joined["cached"]
    release.set()
    await asyncio.gather(*service.explorer._running.values())


async def test_a_slow_pdf_read_does_not_hold_the_waiting_call(service, monkeypatch):
    import time

    release, asked = asyncio.Event(), []
    pid = await provider_waiting_for(service, monkeypatch, release, asked)
    context = service.explorer.context

    def slow_context(*args, **kwargs):
        time.sleep(1.0)
        return context(*args, **kwargs)

    monkeypatch.setattr(service.explorer, "context", slow_context)
    started = time.monotonic()
    first = await service.research_protocol(pid, "Which adjustment method is planned?")
    assert first["status"] == "answering" and time.monotonic() - started < 0.5
    release.set()
    await asyncio.gather(*service.explorer._running.values())


async def test_provider_server_marks_are_not_kept(service, monkeypatch):
    pid = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    service.settings.llm_backend, service.settings.llm_model = "litellm", "anthropic/test"
    marked = answer().model_dump()
    marked["answers"][0]["evidence"][0]["server"] = "source_name"
    decisions = iter([{"answers": [], "source_assessments": []}, {"action": "finish", "answer": marked}])

    async def respond(settings, messages):
        return next(decisions)

    monkeypatch.setattr("ema_rwe.exploration.complete_json", respond)
    result = await service.research_protocol(pid, "Which adjustment method?", poll=False)
    assert result["answer"]["answers"][0]["evidence"][0]["server"] is None
