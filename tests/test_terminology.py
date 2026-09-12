import json

import pytest

from ema_rwe.cli import parser
from ema_rwe.domain import CodeCandidate
from ema_rwe.pdf import Page, search_sections, sections
from ema_rwe.service import Service
from ema_rwe.storage import import_csv
from ema_rwe.terminology import code_variants, proposed_codes, search_units
from ema_rwe.vocabulary import expand

QUESTION = "間質性肺疾患をアウトカムとした研究を探し、どのようなアウトカム定義を使ったかを教えて"


def test_japanese_concept_retrieves_code_only_pdf():
    chunks = sections([Page(1, "8.3 Outcomes\nCase algorithm: two J849 records within 365 days.")])
    result = search_sections(chunks, QUESTION)
    assert result["results"][0]["matched_terms"] == ["J849"]
    terms = search_units(QUESTION, expand(QUESTION))
    assert all(t.isascii() for t in terms)
    assert {"interstitial pneumonia", "interstitial lung disease", "J84.9", "J849"} <= set(terms)
    code = result["query_expansion"]["clinical"]["code_candidates"][0]
    assert code["relation"] == "unspecified_subtype"
    assert code["verification"] == "source_checked"


@pytest.mark.parametrize("query", ["J84.9", "J849", "ICD10: J84.9"])
def test_code_does_not_match_other_code_or_isolated_digit(query):
    chunks = sections([Page(1, "Records: J84.90; J8490; 9 cases; 10 controls."), Page(2, "Code J849.")])
    assert [r["page"] for r in search_sections(chunks, query)["results"]] == [2]


@pytest.mark.parametrize(
    "system,code,variants",
    [
        ("ICD-9-CM", "001.0", ["001.0", "0010"]),
        ("ICD-10-CM", "J849", ["J849", "J84.9"]),
        ("ATC", "L04AA13", ["L04AA13"]),
        ("OMOP concept_id", "000123", ["000123"]),
        ("LOINC", "1234-5", ["1234-5"]),
    ],
)
def test_system_specific_formatting(system, code, variants):
    assert code_variants(CodeCandidate(system=system, code=code)) == variants


def test_local_fts_finds_code_only_metadata(settings, csv_file):
    service = Service(settings)
    import_csv(service.repo, csv_file)
    study = service.repo.get("123")
    study.title, study.description = "Code J849", "Outcome algorithm"
    service.repo.upsert(study)
    assert service.search_studies(QUESTION)["results"][0]["study_id"] == "123"


def test_custom_dictionary_and_numeric_codes(tmp_path, monkeypatch):
    path = tmp_path / "terms.json"
    path.write_text(
        json.dumps(
            [
                {
                    "concept_id": "test_only",
                    "input_terms": ["検証用概念"],
                    "english_terms": ["test concept"],
                    "code_candidates": [
                        {
                            "system": "local test vocabulary",
                            "code": "000123",
                            "verification": "source_checked",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(path))
    expansion = expand("検証用概念")
    code = expansion["clinical"]["code_candidates"][0]
    assert code["code"] == "000123" and code["verification"] == "unverified"
    assert code["origin"] == "local_dictionary"
    chunks = sections([Page(1, "000123"), Page(2, "123")])
    assert [r["page"] for r in search_sections(chunks, "検証用概念")["results"]] == [1]


def test_llm_cannot_claim_verified_mapping():
    code = proposed_codes([{"system": "SNOMED CT", "code": "123", "verification": "source_checked"}])[0]
    assert code.origin == "llm" and code.verification == "unverified"


async def test_plan_has_callable_code_queries_and_unknown_translation_status(settings):
    service = Service(settings)
    try:
        plan = await service.plan_study_search(QUESTION)
        assert plan["target_role"] == "outcome"
        assert "J849" in plan["queries"]
        for request in plan["code_searches"]:
            assert CodeCandidate.model_validate(request["codes"][0]).code == "J84.9"
        assert (await service.plan_study_search("未知の日本語疾患"))["status"] == "needs_client_translation"
    finally:
        await service.close()


def test_cli_typed_codes():
    args = parser().parse_args(["pdf-search", "id", "outcome", "--code", "OMOP concept_id:000123"])
    assert args.code[0].code == "000123"
