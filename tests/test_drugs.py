import json

import httpx
import pytest
from pydantic import ValidationError

from ema_rwe.domain import CodeCandidate, RWEError
from ema_rwe.drugs import SOURCE_URL, drug_expansion, refresh_dictionary
from ema_rwe.pdf import Page, search_sections, sections
from ema_rwe.service import Service
from ema_rwe.storage import import_csv
from ema_rwe.terminology import inline_codes
from ema_rwe.vocabulary import expand


def row(name, inn, code, category="Human"):
    return {
        "category": category,
        "name_of_medicine": name,
        "international_non_proprietary_name_common_name": inn,
        "atc_code_human": code,
        "medicine_url": "https://www.ema.europa.eu/en/medicines/human/EPAR/" + name.lower(),
    }


@pytest.fixture
def drug_file(tmp_path, monkeypatch):
    path = tmp_path / "drugs.json"
    monkeypatch.setenv("EMA_DRUG_DICTIONARY_PATH", str(path))
    payload = {
        "meta": {"timestamp": "2026-09-11"},
        "data": [
            row("Eliquis", "apixaban", "B01AF02"),
            row("Xarelto", "rivaroxaban", "B01AF01"),
            row("Januvia", "sitagliptin", "A10BH01"),
            row("Janumet", "sitagliptin;metformin", "A10BD07"),
            row("AnimalOnly", "apixaban", "B01AF02", "Veterinary"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def japanese_drug_terms(tmp_path, monkeypatch):
    """A local dictionary entry translating Japanese medicine names, as a user would add one."""
    path = tmp_path / "terms.json"
    concept = {
        "concept_id": "apixaban_ja",
        "input_terms": ["エリキュース", "アピキサバン"],
        "english_terms": ["apixaban"],
    }
    path.write_text(json.dumps([concept], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(path))
    return path


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Eliquis", "apixaban"),
        ("apixaban", "Eliquis"),
        ("B01AF02", "Eliquis"),
        ("rivaroxaban", "Xarelto"),
    ],
)
def test_bidirectional_names_and_atc(drug_file, query, expected):
    result = expand(query)["clinical"]
    assert expected in result["english_terms"]
    assert "AnimalOnly" not in result["english_terms"]
    assert result["drugs"]["matches"][0]["source_url"].startswith("https://www.ema.europa.eu/")


@pytest.mark.parametrize(
    "query,expected", [("エリキュースの研究", "apixaban"), ("アピキサバンの研究", "Eliquis")]
)
def test_japanese_name_from_local_dictionary_expands_through_ema_dictionary(
    drug_file, japanese_drug_terms, query, expected
):
    result = expand(query)["clinical"]
    assert expected in result["english_terms"]
    assert "AnimalOnly" not in result["english_terms"]


def test_japanese_name_without_dictionary_entry_is_not_guessed(drug_file, tmp_path, monkeypatch):
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(empty))
    result = expand("エリキュースの研究")["clinical"]
    assert result["english_terms"] == [] and result["drugs"]["total_matches"] == 0


def test_missing_medicines_dictionary_expands_nothing_and_requests_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("EMA_DRUG_DICTIONARY_PATH", str(tmp_path / "absent.json"))
    result = drug_expansion("apixaban Eliquis B01AF02")
    assert result["total_matches"] == 0 and result["english_terms"] == []
    assert result["needs_refresh"] is True


def test_combination_keeps_full_ingredient_set(drug_file):
    assert "Janumet" not in drug_expansion("sitagliptin")["english_terms"]
    combo = drug_expansion("Janumet")
    assert "sitagliptin / metformin" in combo["english_terms"]
    assert "Januvia" not in combo["english_terms"]
    assert combo["matches"][0]["ingredients"] == ["sitagliptin", "metformin"]


def test_pdf_search_by_brand_finds_inn_or_atc_only(drug_file, japanese_drug_terms):
    chunks = sections([Page(1, "Exposure apixaban."), Page(2, "Exposure B01AF02."), Page(3, "B01AF020")])
    assert {r["page"] for r in search_sections(chunks, "エリキュース")["results"]} == {1, 2}
    assert search_sections(sections([Page(1, "Exposure Eliquis.")]), "apixaban")["results"]


def test_fts_bidirectional(drug_file, japanese_drug_terms, settings, csv_file):
    service = Service(settings)
    import_csv(service.repo, csv_file)
    study = service.repo.get("123")
    study.title, study.description = "Eliquis exposure", "Safety"
    service.repo.upsert(study)
    assert service.search_studies("アピキサバン")["results"][0]["study_id"] == "123"


@pytest.mark.parametrize("system,code", [("ATC", "B01AF020"), ("ATC", "J84.9"), ("ICD-10", "J8.49")])
def test_known_code_format_validation(system, code):
    with pytest.raises(ValidationError):
        CodeCandidate(system=system, code=code)


@pytest.mark.parametrize(
    "query,system,code",
    [
        ("atc: b01", "ATC", "B01"),
        ("b01af02", "ATC", "B01AF02"),
        ("B01AF", "ATC", "B01AF"),
        ("J849", "ICD-10 (family inferred; edition unknown)", "J849"),
    ],
)
def test_code_detection(query, system, code):
    result = inline_codes(query)
    assert {(r.system, r.code) for r in result} == {(system, code)}


def test_icd10_cm_not_relabelled_who(drug_file):
    result = expand("codes", codes=[CodeCandidate(system="ICD10CM", code="J84.9")])["clinical"]
    assert result["concepts"] == []


async def test_refresh_cache_and_failed_update_preserves_old_file(drug_file):
    raw = drug_file.read_bytes()
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=raw)

    first = await refresh_dictionary(True, httpx.MockTransport(handle))
    assert first["records"] == 4 and calls == [SOURCE_URL]
    assert (await refresh_dictionary(False, httpx.MockTransport(handle)))["cached"]
    assert len(calls) == 1
    with pytest.raises(RWEError, match="schema"):
        await refresh_dictionary(True, httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert drug_file.read_bytes() == raw


async def test_llm_translation_expands_official_drug_names(drug_file, settings, monkeypatch):
    async def complete(*args):
        return {"queries": ["apixaban cohort"], "synonyms": ["apixaban"]}

    monkeypatch.setattr("ema_rwe.service.complete_json", complete)
    service = Service(settings)
    try:
        plan = await service.plan_study_search("未登録の日本語商品名", use_llm=True)
        assert "Eliquis" in plan["queries"]
        assert any(q["query"] == "B01AF02" for q in plan["code_searches"])
    finally:
        await service.close()
