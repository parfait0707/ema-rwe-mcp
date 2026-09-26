"""Catalogue screening: co-occurrence matching, role-scoped columns, listing and explicit choice."""

import csv
import io

import pytest
from test_comparison import extend_website, seed

from ema_rwe.domain import RWEError, Study
from ema_rwe.ema import BASE
from ema_rwe.selection import COMPACT_KEYS, SearchFilters, country, filter_rows
from ema_rwe.service import Service
from ema_rwe.storage import Repository, fts_match, import_csv


def study(study_id, **fields):
    base = {
        "study_id": study_id,
        "title": f"Study {study_id}",
        "study_type": "Non-interventional study",
        "countries": ["France"],
        "source_url": f"{BASE}/study/{study_id}",
    }
    return Study(**{**base, **fields})


def test_fts_match_requires_cooccurrence_and_scopes_columns():
    assert fts_match([["hepatic", "failure"]], None) == 'NEAR("hepatic" "failure", 3)'
    assert fts_match([["liver"], ["K71"]], ("title", "outcomes")) == '{title outcomes}: ("liver" OR "k71")'
    assert fts_match([[]], None) == ""


def test_multi_word_query_does_not_match_split_words(service):
    service.repo.upsert(study("1", title="Hepatic failure after chemotherapy"))
    service.repo.upsert(study("2", title="Heart failure admissions among patients with hepatic comorbidity"))
    hepatic = service.search_studies("hepatic failure", darwin_only=False)
    assert [r["study_id"] for r in hepatic["results"]] == ["1"]
    assert service.search_studies("failure", darwin_only=False)["total_matches"] == 2


def test_role_scoped_search_uses_catalogue_columns(service):
    service.repo.upsert(study("1", outcomes="Drug-induced liver injury within 90 days"))
    service.repo.upsert(study("2", description="Patients with liver injury history are excluded"))
    service.repo.upsert(study("3", conditions=["Liver injury"], exposures=["Atorvastatin"]))
    ids = lambda result: sorted(r["study_id"] for r in result["results"])
    assert ids(service.search_studies("liver injury", darwin_only=False)) == ["1", "2", "3"]
    assert ids(service.search_studies("liver injury", darwin_only=False, role="outcome")) == ["1"]
    assert ids(service.search_studies("liver injury", darwin_only=False, role="condition")) == ["3"]
    assert ids(service.search_studies("atorvastatin", darwin_only=False, role="exposure")) == ["3"]
    with pytest.raises(RWEError, match="role"):
        service.search_studies("liver injury", role="covariate")


def test_compact_rows_by_default_and_full_on_request(service):
    service.repo.upsert(study("1", description="Long description " * 20, outcomes="bleeding"))
    compact = service.search_studies("bleeding", darwin_only=False)
    assert compact["detail"] == "compact" and "description" not in compact["results"][0]
    assert set(COMPACT_KEYS) <= set(compact["results"][0]) | {"score"}
    assert set(compact["query_expansion"]) == {
        "synonyms",
        "english_terms",
        "related_terms",
        "codes",
        "drug_terms",
        "drugs_need_refresh",
    }
    assert set(compact["catalogue"]) == {"status", "source_type_imports", "browser_refresh_recommended"}
    full = service.search_studies("bleeding", darwin_only=False, detail="full")
    assert full["results"][0]["description"].startswith("Long description")
    assert "clinical" in full["query_expansion"]


def test_candidates_are_listed_only_within_the_listing_limit(settings):
    settings.max_listed_candidates = 3
    service = Service(settings)
    seed(service.repo, 3)
    listed = service.search_studies("opioid")
    assert listed["candidates_listed"] and [c["study_id"] for c in listed["candidates"]] == [
        "123",
        "124",
        "125",
    ]
    assert set(listed["candidates"][0]) <= set(COMPACT_KEYS)
    seed(service.repo, 4)
    unlisted = service.search_studies("opioid")
    assert (
        unlisted["total_matches"] == 4 and not unlisted["candidates_listed"] and unlisted["candidates"] == []
    )
    assert unlisted["max_listed_candidates"] == 3


def test_conditions_facet_counts_catalogue_conditions(service):
    for sid, conditions in (("1", ["Asthma"]), ("2", ["Asthma", "COPD"]), ("3", [])):
        service.repo.upsert(study(sid, title="Inhaler safety", conditions=conditions))
    facets = service.search_studies("inhaler", darwin_only=False)["facets"]
    assert facets["conditions"] == {"asthma": 2, "copd": 1}


async def test_compare_accepts_explicit_study_ids_within_screening_limit(service, website):
    seed(service.repo, 6)
    extend_website(website, 6)
    narrowing = await service.compare_protocols("How defined?", ["opioid"])
    assert narrowing["status"] == "needs_narrowing" and narrowing["candidates_listed"]
    assert "study_ids" in narrowing["next_action"]
    chosen = await service.compare_protocols("How defined?", ["opioid"], study_ids=["124", "127"])
    assert [r["study"]["study_id"] for r in chosen["rows"]] == ["124", "127"]
    assert chosen["search"]["selected_study_ids"] == ["124", "127"]
    assert chosen["search"]["total_matches"] == 2
    for bad in (["124", "124"], ["999"], [], ["123", "124", "125", "126", "127", "128"]):
        with pytest.raises(RWEError, match="study_ids"):
            await service.compare_protocols("How defined?", ["opioid"], study_ids=bad)


async def test_committed_terminology_dictionary_is_the_default(monkeypatch, service):
    monkeypatch.delenv("EMA_TERMINOLOGY_PATH", raising=False)
    plan = await service.plan_study_search("肝障害をアウトカムとした研究")
    assert plan["status"] == "planned"
    assert "liver injury" in plan["query_expansion"]["clinical"]["english_terms"]
    codes = {c["code"] for c in plan["query_expansion"]["clinical"]["code_candidates"]}
    assert {"K71", "K72"} <= codes
    assert all(
        c["verification"] == "unverified" and c["origin"] == "local_dictionary"
        for c in plan["query_expansion"]["clinical"]["code_candidates"]
    )


def test_import_fills_role_columns_from_the_official_headers(settings, tmp_path):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "Study ID",
            "Title",
            "Study type",
            "Medicinal condition to be studied",
            "Additional medical condition(s)",
            "Outcomes",
            "Study drug International non-proprietary name (INN) or common name",
            "Medicinal product name",
            "Anatomical Therapeutic Chemical (ATC) code",
            "Main study objective",
        ]
    )
    writer.writerow(
        [
            "77",
            "Statin hepatic safety",
            "Non-interventional study",
            "Hypercholesterolaemia",
            "Type 2 diabetes mellitus; Hypertension",
            "Hepatic events defined by AST > 3 x ULN",
            "atorvastatin",
            "ATOZET",
            "C10BA05",
            "To compare hepatic event rates",
        ]
    )
    path = tmp_path / "export.csv"
    path.write_text(stream.getvalue(), encoding="utf-8-sig")
    repo = Repository(settings.db_path)
    import_csv(repo, path)
    saved = repo.get("77")
    assert saved.conditions == ["Hypercholesterolaemia", "Type 2 diabetes mellitus", "Hypertension"]
    assert saved.outcomes == "Hepatic events defined by AST > 3 x ULN"
    assert saved.exposures == ["atorvastatin", "ATOZET", "C10BA05"]
    assert saved.objective == "To compare hepatic event rates"
    assert repo.search("hepatic events", None, False, None, role="outcome")[0]["study_id"] == "77"
    assert repo.search("C10BA05", None, False, None, role="exposure")[0]["study_id"] == "77"
    assert repo.search("hypertension", None, False, None, role="condition", filters=SearchFilters())


@pytest.mark.parametrize(
    "alias,catalogue_name",
    [
        ("ドイツ", "Germany"),
        ("イギリス", "United Kingdom"),
        ("usa", "United States"),
        ("韓国", "Korea, Republic of"),
        ("Czech Republic", "Czechia"),
    ],
)
def test_country_aliases_resolve_to_catalogue_spelling(alias, catalogue_name):
    row = {"countries": [catalogue_name], "data_source_types": [], "study_designs": []}
    assert country(alias) == country(catalogue_name)
    assert filter_rows([row], SearchFilters(countries=[alias])) == [row]


def test_unknown_country_passes_through_unchanged():
    assert country("Estonia") == "estonia"
