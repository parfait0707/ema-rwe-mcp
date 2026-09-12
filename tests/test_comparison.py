import json
from pathlib import Path

import pytest
from conftest import labelled
from test_core import sample_analysis

from ema_rwe.cli import parser
from ema_rwe.domain import ProtocolAnswer, RWEError, Study
from ema_rwe.ema import BASE, parse_study
from ema_rwe.selection import SearchFilters
from ema_rwe.service import Service
from ema_rwe.storage import Repository, import_csv


def seed(repo, count):
    for i in range(count):
        repo.upsert(
            Study(
                study_id=str(123 + i),
                title="Opioid safety",
                study_type="Non-interventional study",
                darwin_eu=True,
                countries=["Japan" if i % 2 else "France"],
                data_source_types=["Administrative healthcare records (e.g., claims)"],
                study_designs=["Cohort"],
                source_url=f"{BASE}/study/{123 + i}",
            )
        )


def extend_website(website, count):
    for i in range(1, count):
        website[f"/study/{123 + i}"] = website["/study/123"].replace("<dd>123</dd>", f"<dd>{123 + i}</dd>")


@pytest.mark.parametrize("count", [0, 1, 5, 6, 25])
async def test_count_before_limit_and_no_network_before_narrowing(service, website, count):
    seed(service.repo, count)
    extend_website(website, count)
    preview = service.search_studies("opioid", limit=1)
    assert preview["total_matches"] == count
    assert preview["returned_count"] == min(1, count)
    assert preview["results_are_preview"] == (count > 1)
    assert not service.requests
    result = await service.compare_protocols("What definition is used?", ["opioid", "safety", "opioid"])
    search = result.get("search", result)
    assert search["total_matches"] == count
    if count > 5 or count == 0:
        assert result["status"] == ("needs_narrowing" if count > 5 else "no_matches")
        assert not service.requests
        assert result["pdf_downloads"] == 0
        assert not service.comparisons.root.exists()
    else:
        assert len(result["rows"]) == count
        assert len(result["pending_tools"]) == count * 2
        assert len(list(service.archive.directory.glob("*.pdf"))) == count
        assert result["status"] == "incomplete"
        for row in result["rows"]:
            assert Path(row["json_path"]).exists()
            assert Path(row["pdf_path"]).exists()
            assert row["protocol_data_sources_status"] == "not_analyzed"


async def test_filter_and_unknown_metadata_counts(service):
    seed(service.repo, 6)
    unknown = service.repo.get("123").model_copy(
        update={"countries": [], "data_source_types": [], "study_designs": []}
    )
    service.repo.upsert(unknown)
    preview = service.search_studies("opioid")
    assert preview["status"] == "needs_narrowing"
    assert preview["unknown_metadata_counts"] == dict.fromkeys(
        ["countries", "data_source_types", "study_designs"], 1
    )
    f = SearchFilters(countries=["日本"], data_source_types=["claims", "registry"], study_designs=["cohort"])
    result = service.search_studies("opioid", filters=f)
    assert result["total_matches"] == 3
    assert {r["study_id"] for r in result["results"]} == {"124", "126", "128"}
    assert (
        service.search_studies("opioid", filters=SearchFilters(data_source_types=["ehr"]))["total_matches"]
        == 0
    )
    assert not service.requests


@pytest.mark.parametrize(
    "source,design,source_filter,design_filter",
    [
        ("Disease registry", "Nested case-control", "registry", "case-control"),
        ("Electronic healthcare records (EHR)", "Cross-sectional", "ehr", "cross-sectional"),
        ("Drug dispensing/prescription data", "Ecological", "drug_dispensing_prescription", "ecological"),
        ("Claims", "Self-controlled case series", "claims", "self-controlled"),
    ],
)
async def test_supported_filter_categories(service, source, design, source_filter, design_filter):
    seed(service.repo, 1)
    service.repo.upsert(
        service.repo.get("123").model_copy(update={"data_source_types": [source], "study_designs": [design]})
    )
    result = service.search_studies(
        "opioid", filters=SearchFilters(data_source_types=[source_filter], study_designs=[design_filter])
    )
    assert result["total_matches"] == 1


async def test_every_pdf_and_json_then_validated_answers_survive_restart(service, website):
    seed(service.repo, 5)
    extend_website(website, 5)
    question = "How is diabetes defined?"
    result = await service.compare_protocols(question, ["opioid", "safety"])
    for i, row in enumerate(result["rows"]):
        sid = row["study"]["study_id"]
        extraction = await service.analyze_protocol(sid)
        assert extraction["status"] == "needs_client_extraction"
        assert extraction["next_offset"] is None
        await service.cache_protocol_analysis(
            sid, extraction["source"]["fingerprint"], sample_analysis(), True
        )
        await service.cache_protocol_answer(
            row["source"]["protocol_id"],
            question,
            ProtocolAnswer(answers=sample_analysis().disease_definitions),
        )
        if i == 0:
            partial = await service.get_protocol_comparison(result["comparison_id"])
            assert partial["status"] == "incomplete"
            assert len(partial["pending_tools"]) == 8
    restarted = Service(service.settings)
    try:
        completed = await restarted.get_protocol_comparison(result["comparison_id"])
    finally:
        await restarted.close()
    assert completed["status"] == "complete"
    assert not completed["pending_tools"]
    assert completed["comparison_markdown"].count("Two ICD-10 E11 diagnoses within 365 days") == 10
    assert "planned" in completed["comparison_markdown"]
    assert Path(completed["markdown_path"]).read_text(encoding="utf-8") == completed["comparison_markdown"]
    for row in completed["rows"]:
        saved = json.loads(Path(row["json_path"]).read_text(encoding="utf-8"))
        assert saved == row
        assert saved["status"] == "complete"
        assert saved["protocol_data_sources"][0]["value"] == "Example Primary Care Database"
        assert saved["data_source_types"] == ["Electronic healthcare records (EHR)"]
        assert saved["answer"]["answers"][0]["evidence"][0]["page"] == 1
    # Corrupt/missing PDFs must invalidate previously completed row exports.
    Path(completed["rows"][0]["pdf_path"]).write_bytes(b"corrupt")
    broken = await service.get_protocol_comparison(result["comparison_id"])
    assert broken["status"] == "incomplete"
    assert broken["rows"][0]["status"] == "error"
    assert "answer" not in broken["rows"][0]


async def test_failed_study_does_not_drop_other_pdfs(service, website):
    seed(service.repo, 3)
    extend_website(website, 3)
    website["/study/124"] = website["/study/124"].replace(
        "/node/99/study-documents", "/node/100/study-documents"
    )
    website["/node/100/study-documents"] = "No protocol uploaded"
    result = await service.compare_protocols("What design?", ["opioid"])
    assert len(result["rows"]) == 3
    assert result["status"] == "incomplete"
    error = next(r for r in result["rows"] if r["study"]["study_id"] == "124")
    assert error["error"]["code"] == "PROTOCOL_NOT_FOUND"
    assert Path(error["json_path"]).exists()
    assert error["pdf_path"] is None
    assert len(list(service.archive.directory.glob("*.pdf"))) == 2
    assert len(result["pending_tools"]) == 4


async def test_comparison_does_not_mix_extraction_versions(service):
    seed(service.repo, 1)
    result = await service.compare_protocols("What design?", ["opioid"])
    extraction = await service.analyze_protocol("123")
    saved = await service.cache_protocol_analysis(
        "123", extraction["source"]["fingerprint"], sample_analysis(), True
    )
    saved["source"]["fingerprint"] = "new-version"
    service.repo.save_analysis(service.repo.get("123"), saved)
    result = await service.get_protocol_comparison(result["comparison_id"])
    assert result["status"] == "incomplete"
    assert result["rows"][0]["error"]["code"] == "COMPARISON_SOURCE_CHANGED"


async def test_internal_llm_comparison_uses_same_completion_contract(service, website, monkeypatch):
    seed(service.repo, 2)
    extend_website(website, 2)
    service.settings.llm_model = "mock-model"
    service.settings.llm_base_url = "https://provider.invalid/v1"
    calls = []

    async def extract(*args):
        calls.append("extract")
        return sample_analysis()

    async def finish(*args):
        calls.append("answer")
        return {
            "action": "finish",
            "answer": ProtocolAnswer(answers=sample_analysis().disease_definitions).model_dump(),
        }

    monkeypatch.setattr("ema_rwe.service.extract_with_provider", extract)
    monkeypatch.setattr("ema_rwe.exploration.complete_json", finish)
    result = await service.compare_protocols("How is diabetes defined?", ["opioid"])
    for pending in result["pending_tools"]:
        await getattr(service, pending["tool"])(**pending["arguments"])
    done = await service.get_protocol_comparison(result["comparison_id"])
    assert done["status"] == "complete"
    assert calls.count("extract") == calls.count("answer") == 2
    reused = await service.compare_protocols("How is diabetes defined?", ["opioid"])
    assert reused["status"] == "complete"
    assert not reused["pending_tools"]
    assert len(calls) == 4


async def test_comparison_invalid_ids_and_queries(service):
    with pytest.raises(RWEError, match="comparison_id"):
        await service.get_protocol_comparison("../../outside")
    with pytest.raises(RWEError, match="not found"):
        await service.get_protocol_comparison("cmp_" + "a" * 32)
    with pytest.raises(RWEError, match="queries"):
        await service.compare_protocols("test", [])


def test_csv_and_page_design_metadata(settings, tmp_path):
    csv_path = tmp_path / "designs.csv"
    csv_path.write_text(
        "Study ID,Study title,Study type,Non-interventional study design\n123,Example,Non-interventional study,Cohort|Case-control\n",
        encoding="utf-8",
    )
    repo = Repository(settings.db_path)
    import_csv(repo, csv_path)
    assert repo.get("123").study_designs == ["Cohort", "Case-control"]
    study = parse_study(
        labelled("Study ID", "123") + labelled("Official title and acronym", "Example"),
        "123",
        labelled("Study type", "Non-interventional study")
        + labelled("Non-interventional study design", "Cohort"),
    )
    assert study.study_designs == ["Cohort"]
    assert Study.model_validate(study.model_dump(exclude={"study_designs"})).study_designs == []


def test_comparison_cli_filter_arguments():
    args = parser().parse_args(
        [
            "compare",
            "NVAF definition",
            "--query",
            "NVAF",
            "--query",
            "atrial fibrillation",
            "--country",
            "Japan",
            "--source-type",
            "claims",
            "--study-design",
            "cohort",
        ]
    )
    assert args.query == ["NVAF", "atrial fibrillation"]
    assert args.source_type == ["claims"]
    assert not args.darwin_only
