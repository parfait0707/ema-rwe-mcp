"""Evidence store (spec v1.31): prefetching a source type's protocols, facts derived from analyses,
facts distributed without quotes and their restoration from a local PDF."""

import asyncio
import hashlib
import re
import sqlite3
from typing import get_args

import pytest
from pydantic import BaseModel
from test_core import sample_analysis

from ema_rwe import catalogue
from ema_rwe.config import Settings
from ema_rwe.domain import Extraction, RWEError, Study
from ema_rwe.evidence import FACT_FIELDS, facts_from_analysis, quote_position, restore_quote
from ema_rwe.pdf import extract_pages, page_texts
from ema_rwe.service import Service
from ema_rwe.storage import SCHEMA_VERSION, Repository, import_csv, refresh_from_bundle


def study(study_id: str, types=("claims",), listed=True) -> Study:
    return Study(
        study_id=study_id,
        title=f"Study {study_id}",
        study_type="Non-interventional study",
        data_source_types=list(types),
        protocol_listed=listed,
        source_url=f"https://example.test/study/{study_id}",
    )


@pytest.fixture
def prefetch(settings, monkeypatch):
    service = Service(settings)
    for s in (study("1"), study("2"), study("3", types=("ehr",)), study("4", listed=False)):
        service.repo.upsert(s)
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(catalogue.asyncio, "sleep", sleep)
    return service, sleeps


def run(service, outcomes, **kwargs):
    calls = []

    async def get_protocol(study_id):
        calls.append(study_id)
        outcome = outcomes[study_id].pop(0)
        if outcome != "ok":
            raise RWEError(outcome, outcome)
        return {}

    service.get_protocol = get_protocol
    return asyncio.run(service.prefetch_protocols("claims", **kwargs)), calls


def test_prefetch_takes_only_the_source_types_studies_with_a_listed_protocol(prefetch):
    service, sleeps = prefetch
    summary, calls = run(service, {"1": ["ok"], "2": ["ok"]}, interval=30)
    assert calls == ["1", "2"] and summary["fetched"] == 2 and summary["studies"] == 2
    assert sleeps == [30]


def test_a_rate_limit_widens_the_gap_to_a_minute_and_retries_the_same_study(prefetch):
    service, sleeps = prefetch
    summary, calls = run(service, {"1": ["EMA_RATE_LIMITED", "ok"], "2": ["ok"]}, interval=30)
    assert calls == ["1", "1", "2"] and summary["fetched"] == 2
    assert sleeps == [60, 60] and summary["gap_seconds"] == 60


def test_a_second_rate_limit_in_a_row_pauses_before_retrying(prefetch):
    service, sleeps = prefetch
    outcomes = {"1": ["EMA_RATE_LIMITED", "EMA_RATE_LIMITED", "ok"], "2": ["ok"]}
    summary, calls = run(service, outcomes, interval=30, pause=900)
    assert calls == ["1", "1", "1", "2"] and summary["pauses"] == 1
    assert sleeps == [60, 900, 60, 60]


def test_repeated_rate_limits_stop_the_run_after_three_pauses(prefetch):
    service, _ = prefetch
    summary, calls = run(service, {"1": ["EMA_RATE_LIMITED"] * 10, "2": ["ok"]}, interval=30)
    assert summary["pauses"] == 3 and summary["stopped"] and "2" not in calls


def test_a_rerun_skips_studies_found_without_a_protocol(prefetch):
    service, _ = prefetch
    summary, _ = run(service, {"1": ["PROTOCOL_NOT_FOUND"], "2": ["EMA_UNAVAILABLE"]})
    assert summary["no_protocol"] == 1 and summary["errors"] == {"EMA_UNAVAILABLE": 1}
    summary, calls = run(service, {"2": ["ok"]})
    assert calls == ["2"] and summary["queued"] == 1


# Spec v1.31 item 1, plus missing_information as 'missing'.
SPEC_KINDS = {
    "study_design",
    "cohort.inclusion",
    "cohort.exclusion",
    "cohort.index_date",
    "cohort.baseline_period",
    "cohort.follow_up",
    "cohort.time_window",
    "population",
    "exposure",
    "comparator",
    "outcome",
    "data_source",
    "disease_definition",
    "statistical_analysis",
    "key_note",
    "missing",
}
SHA = "ab" * 32


def fact(value: str, quote: str = "A new-user active comparator cohort study.", **extra) -> dict:
    return {"value": value, "evidence": [{"page": 1, "section": None, "quote": quote}], **extra}


def every_field() -> Extraction:
    return Extraction.model_validate(
        {
            "study_design": fact("Cohort"),
            "cohort": {
                "inclusion_criteria": [fact("Adults")],
                "exclusion_criteria": [fact("Prior use")],
                "index_date": fact("First dispensing"),
                "baseline_period": fact("365 days"),
                "follow_up": fact("Until end of data"),
                "design_schema": {"figure_pages": [1], "time_windows": [fact("Washout 180 days")]},
            },
            "population": fact("Adults with diabetes"),
            "exposure": fact("Drug A"),
            "comparator": fact("Drug B"),
            "outcomes": [fact("Myocardial infarction")],
            "data_sources": [
                fact("Example Claims", usage="planned"),
                fact("Hospital Register", usage="used"),
            ],
            "source_assessments": [
                fact(
                    "Assessed Source",
                    usage="used",
                    types=["claims"],
                    role="outcome",
                    basis="explicit",
                    definition="Inpatient diagnoses",
                    requires_linkage=False,
                )
            ],
            "disease_definitions": [fact("ICD-10 E11")],
            "statistical_analysis": fact("Cox regression"),
            "key_notes": [fact("Interim report in 2027")],
            "missing_information": ["Follow-up end is not stated."],
        }
    )


def saved(analysis: Extraction, study_id: str = "123") -> dict:
    return {
        "analysis": analysis.model_dump(),
        "source": {
            "study_id": study_id,
            "protocol_id": f"pdf_{study_id}_{SHA}",
            "sha256": SHA,
            "extractor": "client-assisted",
            "parser_version": "structural-v24",
            "app_version": "0.5.0",
        },
    }


def field_paths(model: type[BaseModel], prefix: str = ""):
    for name, info in model.model_fields.items():
        nested = [
            a
            for a in get_args(info.annotation)
            if isinstance(a, type) and issubclass(a, BaseModel) and "value" not in a.model_fields
        ]
        if nested:
            yield from field_paths(nested[0], f"{prefix}{name}.")
        else:
            yield prefix + name


def test_every_item_kind_of_the_schema_becomes_a_row_and_source_assessments_do_not():
    rows = facts_from_analysis(saved(every_field()))
    assert {r["item_kind"] for r in rows} == SPEC_KINDS
    assert "Assessed Source" not in {r["value"] for r in rows}
    missing = [r for r in rows if r["item_kind"] == "missing"]
    assert [(r["value"], r["evidence"]) for r in missing] == [("Follow-up end is not stated.", [])]
    assert {(r["pdf_sha256"], r["extractor"], r["parser_version"], r["app_version"]) for r in rows} == {
        (SHA, "client-assisted", "structural-v24", "0.5.0")
    }


def test_every_extraction_field_is_an_item_kind_or_deliberately_left_out():
    """A field added to the schema later cannot drop out of the fact rows unnoticed."""
    left_out = {
        "schema_version",
        "source_assessments",
        "missing_information",
        "cohort.design_schema.figure_pages",
    }
    assert set(field_paths(Extraction)) == {path for path, _ in FACT_FIELDS} | left_out


def test_planned_and_used_data_sources_keep_their_usage():
    rows = facts_from_analysis(saved(every_field()))
    sources = [(r["value"], r["usage"]) for r in rows if r["item_kind"] == "data_source"]
    assert sources == [("Example Claims", "planned"), ("Hospital Register", "used")]
    assert not any("usage" in r for r in rows if r["item_kind"] != "data_source")


PAGE = "8.2 Data sources\nThe study will use Example   Primary\nCare Database (EPCD).\n8.3 Outcomes"
QUOTE = "The study will use Example Primary Care Database (EPCD)."


def test_a_quote_restored_from_its_position_is_the_original_quote():
    assert restore_quote(PAGE, quote_position(PAGE, QUOTE)) == QUOTE


def test_the_window_search_finds_the_quote_when_text_is_added_before_it():
    position = quote_position(PAGE, QUOTE)
    assert restore_quote("Header a newer parser reads\n" + PAGE, position) == QUOTE


def test_nothing_is_restored_where_no_text_has_the_quotes_hash():
    position = quote_position(PAGE, QUOTE)
    assert restore_quote(PAGE.replace("Example", "Exemplar"), position) is None
    assert quote_position(PAGE, "A sentence this page does not hold.") is None
    empty = {
        "quote_start": 0,
        "quote_end": 0,
        "quote_sha256": hashlib.sha256(b"").hexdigest(),
        "quote_length": 0,
    }
    assert restore_quote(PAGE, empty) is None


def test_page_texts_read_a_page_as_extract_pages_does(pdf_bytes):
    assert page_texts(pdf_bytes, {1, 9}) == {1: extract_pages(pdf_bytes)[0].text}


@pytest.fixture
async def analysed(service):
    """Study 123 extracted locally (sample_analysis) with its PDF kept."""
    first = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis(), True)
    return service


def other_user(tmp_path, name: str, csv_file) -> Service:
    """Another user's database with the catalogue (studies 123 and 456) and no analyses or PDFs."""
    settings = Settings(
        db_path=tmp_path / name / "db.sqlite3", cache_dir=tmp_path / name / "http", interval=0
    )
    service = Service(settings)
    import_csv(service.repo, csv_file)
    return service


def distributed(service: Service, tmp_path) -> list[dict]:
    output = tmp_path / "distributed.sqlite3"
    summary = service.export_facts(output)
    assert summary["facts_written"] == 3 and summary["facts_without_position"] == 0
    return Repository(output).prebuilt_facts()


async def test_distributed_facts_show_no_quote_until_the_pdf_is_kept_here(
    analysed, tmp_path, csv_file, pdf_bytes
):
    rows = distributed(analysed, tmp_path)
    user = other_user(tmp_path, "user", csv_file)
    user.repo.replace_prebuilt_facts({"123"}, rows)

    without_pdf = user.find_facts(study_ids=["123", "456"])
    assert without_pdf["origins"] == {"123": "prebuilt", "456": "none"} and without_pdf["no_facts"] == ["456"]
    assert {f["status"] for f in without_pdf["facts"]} == {"verified_at_build"}
    assert all(e["quote"] == "" for f in without_pdf["facts"] for e in f["evidence"])
    assert all(f["quote_note"] for f in without_pdf["facts"])

    user.archive.save("123", pdf_bytes, {})
    with_pdf = user.find_facts(study_ids=["123"])
    assert {f["status"] for f in with_pdf["facts"]} == {"verified_local"}
    local = facts_from_analysis(analysed.repo.analysis("123"))
    assert [(f["value"], f["evidence"]) for f in with_pdf["facts"]] == [
        (f["value"], f["evidence"]) for f in local
    ]
    assert with_pdf["network_requests"] == 0


async def test_a_local_analysis_wins_over_distributed_facts(analysed, tmp_path):
    rows = distributed(analysed, tmp_path)
    analysed.repo.replace_prebuilt_facts({"123"}, [dict(r, value="From the distribution") for r in rows])
    before = len(analysed.requests)
    found = analysed.find_facts(study_ids=["123"])
    assert found["origins"] == {"123": "local"}
    assert "From the distribution" not in {f["value"] for f in found["facts"]}
    assert len(analysed.requests) == before


async def test_the_scope_follows_source_types_and_keeps_studies_without_facts(analysed, csv_file):
    import_csv(analysed.repo, csv_file)  # 123 is EHR and claims
    analysed.repo.upsert(study("456", types=("registry",)))  # lists a protocol, has no facts
    analysed.repo.upsert(study("457", types=("registry",), listed=False))  # lists none: out of scope
    claims = analysed.find_facts(source_types=["claims"], text="icd-10 e11")
    assert claims["origins"] == {"123": "local"} and claims["no_facts"] == []
    assert [f["item_kind"] for f in claims["facts"]] == ["disease_definition"]
    registry = analysed.find_facts(source_types=["registry"])
    assert (
        registry["origins"] == {"456": "none"} and registry["no_facts"] == ["456"] and not registry["facts"]
    )
    assert analysed.find_facts()["origins"] == {"123": "local"}  # neither: every study with facts
    limited = analysed.find_facts(study_ids=["123"], item_kinds=["study_design", "data_source"], limit=1)
    assert [f["item_kind"] for f in limited["facts"]] == ["study_design"]
    assert limited["total_facts"] == 2 and limited["truncated"]


async def test_a_fact_whose_quote_cannot_be_placed_is_not_exported(analysed, tmp_path):
    result = analysed.repo.analysis("123")
    result["analysis"]["study_design"]["evidence"][0]["quote"] = "A passage this protocol does not hold."
    analysed.repo.save_analysis(analysed.repo.get("123"), result)
    summary = analysed.export_facts(tmp_path / "out.sqlite3")
    assert summary["facts_without_position"] == 1 and summary["facts_written"] == 2
    rows = Repository(tmp_path / "out.sqlite3").prebuilt_facts()
    assert "study_design" not in {r["item_kind"] for r in rows}
    quotes = {e["quote"] for f in facts_from_analysis(result) for e in f["evidence"]}
    assert rows and not any(q in str(v) for q in quotes for r in rows for v in r.values())


def test_distributed_facts_leave_the_schema_version_at_6(tmp_path):
    """Published snapshots carry schema 6 and the update checks it: a new version would stop every update."""
    repo = Repository(tmp_path / "db.sqlite3")
    repo.replace_prebuilt_facts({"123"}, [])
    assert SCHEMA_VERSION == 6
    with sqlite3.connect(tmp_path / "db.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def row(value: str, study_id: str = "123") -> dict:
    return {
        "pdf_sha256": SHA,
        "study_id": study_id,
        "protocol_id": f"pdf_{study_id}_{SHA}",
        "item_kind": "outcome",
        "value": value,
        "usage": None,
        "page": 1,
        "section": None,
        "quote_start": 0,
        "quote_end": 10,
        "quote_sha256": SHA,
        "quote_length": 10,
        "extractor": "client-assisted",
        "parser_version": "structural-v24",
        "app_version": "0.6.0",
        "built_at": "2026-11-21T00:00:00+00:00",
    }


def test_a_newer_snapshot_replaces_the_distributed_facts_and_one_without_them_keeps_them(tmp_path, csv_file):
    def snapshot(name: str, values: list[str] | None) -> Repository:
        repo = Repository(tmp_path / name)
        import_csv(repo, csv_file)
        if values is not None:
            repo.replace_prebuilt_facts({"123"}, [row(v) for v in values])
        return repo

    user = Repository(tmp_path / "user.sqlite3")
    user.replace_prebuilt_facts({"123"}, [row("installed earlier")])
    assert refresh_from_bundle(snapshot("without.sqlite3", None).path, user.path)
    assert [r["value"] for r in user.prebuilt_facts()] == ["installed earlier"]
    assert refresh_from_bundle(snapshot("with.sqlite3", ["new"]).path, user.path)
    assert [r["value"] for r in user.prebuilt_facts()] == ["new"]


async def test_the_facts_page_escapes_protocol_text_and_loads_nothing(settings, tmp_path, csv_file):
    service = Service(settings)
    import_csv(service.repo, csv_file)
    hostile = '<script>alert(1)</script><img src="https://example.test/x.png">'
    service.repo.replace_prebuilt_facts({"123"}, [row(hostile) | {"section": "<b>8.1</b>"}])
    summary = service.write_facts_site(tmp_path / "facts.html", study_ids=["123", "456"])
    page = (tmp_path / "facts.html").read_text(encoding="utf-8")
    assert summary["facts"] == 1 and summary["no_facts"] == 1
    assert not re.search(
        r"<(script|link|img|iframe|object|embed)\b|<[^>]*\bsrc(set)?=|@import|url\(", page, re.IGNORECASE
    )
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;b&gt;8.1&lt;/b&gt;" in page
    assert 'href="https://catalogues.ema.europa.eu/study/123"' in page
    assert "引用は PDF 取得後に表示" in page and "456" in page


def test_find_facts_refuses_ids_that_are_not_study_ids(settings):
    with pytest.raises(RWEError) as error:
        Service(settings).find_facts(study_ids=["../x"])
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "text",
    [
        "  ICD-O-\n  3 codes­ here  ",
        "Straße ΣΑΣ  non-breaking\tand - spaced",
        "co-­\nmorbidity, -  dash at the start",
    ],
)
def test_a_quote_is_cut_verbatim_where_normalize_quote_finds_it(text):
    """The verbatim cut maps each normalized character back to the page; both normalizations must agree."""
    from ema_rwe.evidence import _normalized_with_origin
    from ema_rwe.pdf import normalize_quote

    assert _normalized_with_origin(text)[0] == normalize_quote(text)
    quote = text.strip()[3:20]
    position = quote_position(text, quote)
    assert position and normalize_quote(restore_quote(text, position)) == normalize_quote(quote)
