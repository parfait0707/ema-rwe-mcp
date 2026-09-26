"""Match provenance and the analogous-concept fallback: a study matched only through a different
(broader, sibling or associated) concept is never presented as a match for the requested concept."""

import json

import pytest
from test_comparison import seed
from test_screening import study

from ema_rwe.cli import parser
from ema_rwe.domain import AnalogousTerm, RWEError
from ema_rwe.selection import SearchFilters
from ema_rwe.terminology import Concept

T1DM = {
    "concept_id": "toy_type_1_diabetes",
    "input_terms": ["1型糖尿病"],
    "english_terms": ["type 1 diabetes"],
    "analogous_terms": [{"term": "diabetes mellitus", "relation": "broader"}],
}


@pytest.fixture
def dictionary(tmp_path, monkeypatch):
    path = tmp_path / "terminology.json"
    path.write_text(json.dumps([T1DM]), encoding="utf-8")
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(path))
    return Concept.model_validate(T1DM)


def ids(result):
    return sorted(r["study_id"] for r in result["results"])


def test_rows_report_the_terms_they_matched(service, dictionary):
    service.repo.upsert(study("1", title="Hypoglycaemia in type 1 diabetes"))
    result = service.search_studies("1型糖尿病", darwin_only=False)
    row = result["results"][0]
    assert result["match_scope"] == row["match_basis"] == "concept"
    assert dictionary.english_terms[0] in row["matched_terms"]


def test_broad_group_does_not_fire_inside_a_specific_concept(service, dictionary):
    # Given a study about diabetes in general and one about the specific concept
    service.repo.upsert(study("1", title="Hypoglycaemia in type 1 diabetes"))
    service.repo.upsert(study("2", title="Cardiovascular outcomes in diabetes"))
    # When the specific concept is searched, the general "diabetes" vocabulary group stays silent
    assert ids(service.search_studies("1型糖尿病", darwin_only=False)) == ["1"]
    # while the general term on its own still reaches both studies
    assert ids(service.search_studies("糖尿病", darwin_only=False)) == ["1", "2"]


def test_zero_hits_offer_analogous_studies_without_network(service, dictionary):
    service.repo.upsert(study("2", title="Renal outcomes in diabetes mellitus"))
    result = service.search_studies("1型糖尿病", darwin_only=False)
    fallback = result["analogous_fallback"]
    assert result["total_matches"] == 0 and fallback["status"] == "available"
    offered = fallback["analogous_terms"][0]
    assert offered == {
        **dictionary.analogous_terms[0].model_dump(),
        "concept_id": dictionary.concept_id,
        "study_count": 1,
    }
    assert [c["study_id"] for c in fallback["candidates"]] == ["2"]
    assert not service.requests


def test_analogous_scope_excludes_concept_matches_and_labels_rows(service, dictionary):
    service.repo.upsert(study("1", title="Type 1 diabetes mellitus registry"))
    service.repo.upsert(study("2", title="Renal outcomes in diabetes mellitus"))
    result = service.search_studies("1型糖尿病", darwin_only=False, match_scope="analogous")
    assert ids(result) == ["2"]
    assert result["results"][0]["match_basis"] == "analogous"
    assert result["results"][0]["matched_terms"] == [dictionary.analogous_terms[0].term]
    assert result["analogous_terms"][0]["relation"] == dictionary.analogous_terms[0].relation
    assert "analogous_fallback" not in result


def test_concept_hits_carry_no_fallback(service, dictionary):
    service.repo.upsert(study("1", title="Hypoglycaemia in type 1 diabetes"))
    assert "analogous_fallback" not in service.search_studies("1型糖尿病", darwin_only=False)


def test_unknown_concept_asks_the_caller_for_analogous_terms(service, dictionary):
    service.repo.upsert(study("3", title="Renal impairment in older adults"))
    fallback = service.search_studies("zzqx disease", darwin_only=False)["analogous_fallback"]
    assert fallback["status"] == "no_analogous_terms"
    caller = [AnalogousTerm(term="renal impairment", relation="broader")]
    result = service.search_studies(
        "zzqx disease", darwin_only=False, match_scope="analogous", analogous=caller
    )
    assert ids(result) == ["3"] and result["analogous_terms"][0]["concept_id"] is None


def test_known_terms_without_studies_report_no_matches(service, dictionary):
    fallback = service.search_studies("1型糖尿病", darwin_only=False)["analogous_fallback"]
    assert fallback["status"] == "no_matches" and fallback["total_matches"] == 0


def test_invalid_scope_and_term_are_rejected(service, dictionary):
    with pytest.raises(RWEError, match="scope"):
        service.repo.search("x", None, False, None, scope="nearby")
    with pytest.raises(ValueError):
        AnalogousTerm(term="renal impairment", relation="synonym")
    with pytest.raises(ValueError):
        AnalogousTerm(term="腎障害", relation="broader")


async def test_comparison_labels_analogous_rows(service, dictionary):
    seed(service.repo, 1)
    caller = [AnalogousTerm(term="opioid safety", relation="sibling")]
    none = await service.compare_protocols("How is it defined?", ["zzqx toxicity"], analogous=caller)
    assert none["status"] == "no_matches" and none["analogous_fallback"]["total_matches"] == 1
    result = await service.compare_protocols(
        "How is opioid safety defined?", ["zzqx toxicity"], match_scope="analogous", analogous=caller
    )
    row = result["rows"][0]
    assert row["match"] == {"basis": "analogous", "terms": [caller[0].term]}
    assert f"{caller[0].term}（{caller[0].relation}）" in result["comparison_markdown"]
    assert "類縁概念での一致" in result["comparison_markdown"]


async def test_analogous_scope_excludes_concept_matches_of_every_query_variant(service, dictionary):
    # A study matching the concept of variant 1 must not return as "analogous" through variant 2.
    service.repo.upsert(study("1", title="Type 1 diabetes registry; diabetes mellitus care"))
    service.repo.upsert(study("2", title="Renal outcomes in diabetes mellitus"))
    result = await service.compare_protocols(
        "q", ["1型糖尿病", "hypoglycaemia"], match_scope="analogous", analogous=dictionary.analogous_terms
    )
    assert result["search"]["total_matches"] == 1
    assert {r["study"]["study_id"] for r in result["rows"]} == {"2"}


def test_filtered_out_concept_is_not_reported_as_absent(service, dictionary):
    service.repo.upsert(study("1", title="Hypoglycaemia in type 1 diabetes", countries=["Spain"]))
    service.repo.upsert(study("2", title="Renal outcomes in diabetes mellitus"))
    result = service.search_studies(
        "1型糖尿病", darwin_only=False, filters=SearchFilters(countries=["France"])
    )
    fallback = result["analogous_fallback"]
    assert result["total_matches"] == 0
    assert fallback["status"] == "concept_filtered_out"
    assert fallback["concept_index_matches_before_filters"] == 1


def test_fallback_respects_status_filter(service, dictionary):
    service.repo.upsert(study("2", title="Renal outcomes in diabetes mellitus", status="Finalised"))
    result = service.search_studies("1型糖尿病", darwin_only=False, status=["Ongoing"])
    assert result["analogous_fallback"]["total_matches"] == 0


def test_caller_spelling_is_merged_with_the_dictionary_term(service, dictionary):
    service.repo.upsert(study("2", title="Renal outcomes in diabetes mellitus"))
    caller = [AnalogousTerm(term=dictionary.analogous_terms[0].term.title(), relation="sibling")]
    result = service.search_studies("1型糖尿病", darwin_only=False, match_scope="analogous", analogous=caller)
    assert result["results"][0]["matched_terms"] == [dictionary.analogous_terms[0].term]
    assert [t["relation"] for t in result["analogous_terms"]] == [dictionary.analogous_terms[0].relation]


def test_empty_analogous_scope_explains_itself(service, dictionary):
    result = service.search_studies("1型糖尿病", darwin_only=False, match_scope="analogous")
    assert result["total_matches"] == 0 and result["analogous_note"].startswith("No study matched")


def test_cli_analogous_arguments():
    args = parser().parse_args(
        ["search", "zzqx", "--match-scope", "analogous", "--analogous", "renal impairment:broader"]
    )
    assert args.match_scope == "analogous"
    assert args.analogous == [AnalogousTerm(term="renal impairment", relation="broader")]
    with pytest.raises(SystemExit):
        parser().parse_args(["search", "zzqx", "--analogous", "renal impairment:synonym"])
