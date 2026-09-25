import json
import sqlite3
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError
from test_comparison import extend_website, seed
from test_core import sample_analysis

from ema_rwe.config import Settings
from ema_rwe.domain import Extraction, ProtocolAnswer, RWEError, SourceAssessment, SourcePreference
from ema_rwe.selection import SearchFilters
from ema_rwe.source_types import assess_row, select_rows
from ema_rwe.storage import SCHEMA_VERSION, Repository, import_csv

QUOTE = "The study will use Example Primary Care Database electronic health records to define the cohort."


def assessment(**kwargs):
    return SourceAssessment(
        **{
            "value": "Example Primary Care Database",
            "types": ["ehr"],
            "role": "cohort",
            "basis": "explicit",
            "usage": "planned",
            "requires_linkage": False,
            "definition": "Cohort defined using electronic health records.",
            "evidence": [{"page": 2, "section": "9 Data sources", "quote": QUOTE}],
        }
        | kwargs
    )


def row_with(*assessments, status="complete"):
    return {
        "study": {"data_source_types": ["Claims"]},
        "status": status,
        "analysis": {"source_assessments": [a.model_dump() for a in assessments]},
        "answer": {"source_assessments": [a.model_dump() for a in assessments]},
    }


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, "matched"),
        ({"requires_linkage": True}, "partial"),
        ({"basis": "inferred"}, "possible"),
        ({"types": ["registry"]}, "other_type"),
        ({"usage": "candidate"}, "unknown"),
        ({"usage": "unclear"}, "unknown"),
        ({"role": "outcome"}, "unknown"),
        ({"role": "unclear"}, "unknown"),
    ],
)
def test_role_and_usage_not_catalogue_determine_suitability(changes, expected):
    result = assess_row(row_with(assessment(**changes)), SourcePreference(types=["ehr"], role="cohort"))
    assert result["status"] == expected
    assert result["assessments"][0]["usage"] == changes.get("usage", "planned")


def test_hybrid_definitions_and_conflicts_are_not_silent_matches():
    items = [assessment(types=["claims"], role="cohort"), assessment(role="outcome", requires_linkage=True)]
    assert (
        assess_row(row_with(*items), SourcePreference(types=["claims"], role="cohort"))["status"] == "matched"
    )
    assert (
        assess_row(row_with(*items), SourcePreference(types=["claims"], role="outcome"))["status"]
        == "other_type"
    )
    assert (
        assess_row(row_with(*items), SourcePreference(types=["ehr"], role="outcome"))["status"] == "partial"
    )
    conflict = assess_row(
        row_with(assessment(), assessment(types=["claims"])), SourcePreference(types=["ehr"])
    )
    assert conflict["status"] == "conflicting" and conflict["conflicts"]
    assert assess_row(row_with(), SourcePreference(types=["ehr"]))["status"] == "unknown"
    incomplete = assess_row(row_with(assessment(), status="error"), SourcePreference(types=["ehr"]))
    assert incomplete["status"] == "unknown"


def test_general_or_different_outcome_does_not_prove_question_match():
    row = row_with(assessment(types=["claims"], role="outcome"))
    row["answer"]["source_assessments"] = []
    assert assess_row(row, SourcePreference(types=["claims"], role="outcome"))["status"] == "unknown"
    row = row_with(
        assessment(types=["claims"], role="outcome", definition="Stroke"),
        assessment(types=["ehr"], role="outcome", definition="Laboratory values"),
    )
    assert not assess_row(row, SourcePreference(types=["claims"], role="outcome"))["conflicts"]
    row = row_with(assessment(), assessment(requires_linkage=True))
    assert assess_row(row, SourcePreference(types=["ehr"]))["status"] == "conflicting"


def test_preferred_definitions_rank_first_without_dropping_other_studies():
    rows = [
        row_with(),
        row_with(assessment(types=["registry"])),
        row_with(assessment()),
        row_with(assessment(requires_linkage=True)),
    ]
    for index, row in enumerate(rows):
        row["study"].update(study_id=str(index), title=f"Study {index}")
    result = {
        "rows": rows,
        "status": "complete",
        "search": {
            "source_preference": SourcePreference(types=["ehr"], role="cohort").model_dump(),
            "max_comparison_studies": 5,
        },
    }
    select_rows(result)
    assert result["displayed_study_ids"] == ["2", "3", "1", "0"]
    assert len(result["screening_summary"]) == len(rows)
    result["search"]["source_preference"]["mode"] = "only"
    select_rows(result)
    assert result["displayed_study_ids"] == ["2"]
    assert len(result["rows"]) == 4


def typed_pdf(website):
    with pymupdf.open(stream=website["/system/files/protocol.pdf"], filetype="pdf") as doc:
        page = doc.new_page()
        page.insert_text((50, 50), "9 Data sources\n" + QUOTE, fontsize=9)
        website["/system/files/protocol.pdf"] = doc.tobytes()


async def complete_rows(service, result, include_sources=True):
    for row in result["rows"]:
        sid = row["study"]["study_id"]
        current = await service.analyze_protocol(sid)
        analysis = sample_analysis()
        if include_sources:
            analysis.source_assessments = [assessment()]
        await service.cache_protocol_analysis(sid, current["source"]["fingerprint"], analysis, True)
        await service.cache_protocol_answer(
            row["source"]["protocol_id"],
            result["question"],
            ProtocolAnswer(
                answers=analysis.disease_definitions, source_assessments=analysis.source_assessments
            ),
        )
    return await service.get_protocol_comparison(result["comparison_id"], detail="full")


async def test_caller_assessment_cache_reuse_and_separate_official_type(service, website):
    seed(service.repo, 1)
    typed_pdf(website)
    preferred = SourcePreference(types=["ehr"], role="cohort")
    initial = await service.compare_protocols("How defined?", ["opioid"], source_preference=preferred)
    done = await complete_rows(service, initial)
    assert done["rows"][0]["source_suitability"]["status"] == "matched"
    assert done["rows"][0]["study"]["data_source_types"] == ["Electronic healthcare records (EHR)"]
    assert done["rows"][0]["source_suitability"]["assessments"][0]["usage"] == "planned"
    assert "p. 2" in done["comparison_markdown"]
    requests = len(service.requests)
    reused = await service.compare_protocols(
        "How defined?", ["opioid"], source_preference=SourcePreference(types=["claims"], role="outcome")
    )
    assert reused["status"] == "complete" and not reused["pending_tools"]
    assert reused["rows"][0]["source_suitability"]["status"] == "unknown"
    assert len(service.requests) == requests
    assert reused["rows"][0]["source"]["protocol_id"] == initial["rows"][0]["source"]["protocol_id"]


async def test_configured_provider_assessment_and_quote_validation(service, website, monkeypatch):
    seed(service.repo, 1)
    typed_pdf(website)
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    analysis = sample_analysis()
    analysis.source_assessments = [assessment()]

    async def extract(*args):
        return analysis

    async def finish(*args):
        return {"action": "finish", "answer": ProtocolAnswer(source_assessments=[assessment()]).model_dump()}

    monkeypatch.setattr("ema_rwe.service.extract_with_provider", extract)
    monkeypatch.setattr("ema_rwe.exploration.complete_json", finish)
    initial = await service.compare_protocols(
        "Source?", ["opioid"], source_preference=SourcePreference(types=["ehr"])
    )
    for pending in initial["pending_tools"]:
        await getattr(service, pending["tool"])(**pending["arguments"])
    done = await service.get_protocol_comparison(initial["comparison_id"])
    assert done["status"] == "complete"
    assert done["rows"][0]["source_suitability"]["status"] == "matched"
    pid = done["rows"][0]["source"]["protocol_id"]
    for wrong in (assessment(value="Invented Database"), assessment(evidence=[{"page": 1, "quote": QUOTE}])):
        with pytest.raises(RWEError, match="(evidence|quote|Quote)"):
            await service.cache_protocol_answer(pid, "Source?", ProtocolAnswer(source_assessments=[wrong]))
    # Rejected replacements leave the valid answer intact.
    assert (await service.research_protocol(pid, "Source?"))["answer"]["source_assessments"][0][
        "value"
    ] == assessment().value


async def test_independent_screening_and_display_limits_keep_all_artifacts(service, website):
    seed(service.repo, 3)
    extend_website(website, 3)
    typed_pdf(website)
    service.settings.max_screening_studies = 3
    service.settings.max_comparison_studies = 2
    initial = await service.compare_protocols(
        "How defined?", ["opioid"], source_preference=SourcePreference(types=["ehr"])
    )
    assert len(initial["rows"]) == 3 and initial["displayed_study_ids"] == []
    with pytest.raises(RWEError, match="Finish all"):
        await service.get_protocol_comparison(initial["comparison_id"], ["123"])
    done = await complete_rows(service, initial)
    assert done["selection_status"] == "needs_selection"
    assert len(done["screening_summary"]) == 3 and done["displayed_study_ids"] == []
    for ids in (["123", "124", "125"], ["123", "123"], ["999"], []):
        with pytest.raises(RWEError, match="unique eligible"):
            await service.get_protocol_comparison(initial["comparison_id"], ids)
    chosen = await service.get_protocol_comparison(initial["comparison_id"], ["124", "125"])
    assert chosen["selection_status"] == "ready" and chosen["displayed_study_ids"] == ["124", "125"]
    assert len(chosen["rows"]) == 3
    for row in chosen["rows"]:
        assert Path(row["pdf_path"]).exists() and Path(row["json_path"]).exists()
    # Limits are comparison snapshots, unaffected by later settings changes.
    service.settings.max_comparison_studies = 1
    assert (await service.get_protocol_comparison(initial["comparison_id"]))["displayed_study_ids"] == [
        "124",
        "125",
    ]


async def test_screening_limit_can_exceed_five(service, website):
    seed(service.repo, 6)
    extend_website(website, 6)
    service.settings.max_screening_studies = 6
    result = await service.compare_protocols("How defined?", ["opioid"])
    assert len(result["rows"]) == 6 and len(result["pending_tools"]) == 12
    assert result["search"]["max_comparison_studies"] == 5
    assert result["displayed_study_ids"] == []
    assert len(list(service.archive.directory.glob("*.pdf"))) == 6


async def test_catalogue_source_filter_narrows_before_pdf_screening(service, website):
    seed(service.repo, 1)
    typed_pdf(website)
    excluded = await service.compare_protocols(
        "How defined?", ["opioid"], filters=SearchFilters(data_source_types=["ehr"])
    )
    assert excluded["status"] == "no_matches" and excluded["rows"] == [] and not service.requests
    initial = await service.compare_protocols(
        "How defined?",
        ["opioid"],
        filters=SearchFilters(data_source_types=["claims"]),
        source_preference=SourcePreference(types=["ehr"]),
    )
    assert initial["search"]["filters"]["data_source_types"] == ["claims"]
    assert initial["search"]["source_preference"]["types"] == ["ehr"]
    done = await complete_rows(service, initial, include_sources=False)
    assert done["rows"][0]["source_suitability"]["status"] == "unknown"
    assert Path(done["rows"][0]["json_path"]).exists()


async def test_unknown_source_preferences_cannot_evade_screening_gate(service):
    seed(service.repo, 6)
    for study_id in range(123, 129):
        service.repo.upsert(service.repo.get(str(study_id)).model_copy(update={"data_source_types": []}))
    result = await service.compare_protocols(
        "How defined?", ["opioid", "safety"], source_preference=SourcePreference(types=["claims"])
    )
    assert result["total_matches"] == 6 and result["status"] == "needs_narrowing"
    assert result["unknown_metadata_counts"]["data_source_types"] == 6
    assert not service.requests


async def test_old_analysis_preserved_during_schema_upgrade(service):
    old = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", old["source"]["fingerprint"], sample_analysis(), True)
    saved = service.repo.analysis("123")
    saved["analysis"]["schema_version"] = "0.1"
    saved["analysis"].pop("source_assessments")
    saved["source"]["fingerprint"] = "legacy-fingerprint"
    service.repo.save_analysis(service.repo.get("123"), saved)
    current = await service.analyze_protocol("123")
    assert current["status"] == "needs_client_extraction" and current["source"]["schema_version"] == "0.2"
    assert service.repo.analysis("123") is None
    with service.repo.connection() as db:
        history = db.execute(
            "SELECT body FROM analysis_history WHERE fingerprint='legacy-fingerprint'"
        ).fetchone()
    assert json.loads(history[0])["analysis"]["schema_version"] == "0.1"


async def test_freshness_only_reuse_does_not_accumulate_analysis_history(service):
    current = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", current["source"]["fingerprint"], sample_analysis(), True)
    saved = service.repo.analysis("123")
    saved["source"]["documents_checked_at"] = "2026-09-13T00:00:00+00:00"
    service.repo.save_analysis(service.repo.get("123"), saved)
    with service.repo.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM analysis_history").fetchone()[0] == 0


async def test_csv_without_type_preserves_checked_official_type_and_analysis(service, tmp_path):
    old = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", old["source"]["fingerprint"], sample_analysis(), True)
    before = service.repo.get("123")
    path = tmp_path / "new.csv"
    path.write_text(
        "Study ID,Title,Study type\n123,Updated title,Non-interventional study\n", encoding="utf-8"
    )
    import_csv(service.repo, path)
    after = service.repo.get("123")
    assert after.title == "Updated title"
    assert after.data_source_types == before.data_source_types
    assert after.data_source_types_source == before.data_source_types_source
    assert after.data_source_types_checked_at == before.data_source_types_checked_at
    assert service.repo.analysis("123")["analysis"]["data_sources"]


def test_version_two_database_and_new_limits(settings, monkeypatch):
    with sqlite3.connect(settings.db_path) as db:
        db.execute("PRAGMA user_version=2")
    repo = Repository(settings.db_path)
    with repo.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    monkeypatch.setenv("EMA_MAX_SCREENING_STUDIES", "12")
    monkeypatch.setenv("EMA_MAX_COMPARISON_STUDIES", "4")
    assert Settings().max_screening_studies == 12 and Settings().max_comparison_studies == 4
    for value in (0, -1, 1001, True):
        with pytest.raises(ValueError):
            Settings(max_screening_studies=value)
    with pytest.raises(ValidationError):
        Extraction(schema_version="0.1")
