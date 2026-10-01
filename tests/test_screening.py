"""Catalogue screening: co-occurrence matching, role-scoped columns, listing and explicit choice."""

import csv
import io
import json
from pathlib import Path

import pymupdf
import pytest
from test_comparison import extend_website, seed

from ema_rwe import terminology
from ema_rwe.domain import RWEError, Study
from ema_rwe.ema import BASE
from ema_rwe.pdf import text_layer
from ema_rwe.selection import COMPACT_KEYS, SearchFilters, country, filter_rows
from ema_rwe.service import Service
from ema_rwe.storage import Repository, fts_match, import_csv
from ema_rwe.vocabulary import expand

EXAMPLE = Path(__file__).resolve().parents[1] / "data" / "terminology.example.json"


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
    screening_only = {
        "rank_features",
        "protocol_available",
        "out_of_scope",
    }  # set by compare_protocols ranking
    assert set(COMPACT_KEYS) <= set(compact["results"][0]) | {"score"} | screening_only
    assert set(compact["query_expansion"]) == {
        "synonyms",
        "english_terms",
        "related_terms",
        "analogous_terms",
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


async def test_default_has_no_disease_dictionary_and_asks_the_client(tmp_path, monkeypatch, service):
    # Given no EMA_TERMINOLOGY_PATH and an empty data/dictionaries/
    monkeypatch.delenv("EMA_TERMINOLOGY_PATH", raising=False)
    monkeypatch.setattr(terminology, "default_dictionary_dir", lambda: tmp_path / "dictionaries")
    # When a Japanese clinical question is planned, even one with a translatable design word
    plan = await service.plan_study_search("肝障害のコホート研究")
    # Then the client must translate it, guided by ICD-10
    assert plan["status"] == "needs_client_translation" and plan["client_expansion"]["required"]
    assert "ICD-10" in plan["client_expansion"]["instruction"]
    assert plan["dictionaries"] == [] and plan["method"] == "client_expansion"
    assert not plan["query_expansion"]["clinical"]["english_terms"]
    assert service.catalogue_status()["dictionaries"] == []


async def test_example_dictionary_works_as_a_user_dictionary(tmp_path, monkeypatch, service):
    folder = tmp_path / "dictionaries"
    folder.mkdir()
    (folder / EXAMPLE.name).write_bytes(EXAMPLE.read_bytes())
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(folder))
    plan = await service.plan_study_search("肝障害をアウトカムとした研究")
    concept = next(
        c for c in json.loads(EXAMPLE.read_text(encoding="utf-8")) if c["concept_id"] == "liver_injury"
    )
    assert plan["status"] == "planned" and plan["dictionaries"] == [EXAMPLE.name]
    assert set(concept["english_terms"]) <= set(plan["query_expansion"]["clinical"]["english_terms"])
    codes = plan["query_expansion"]["clinical"]["code_candidates"]
    assert {c["code"] for c in concept["code_candidates"]} <= {c["code"] for c in codes}
    assert all(c["origin"] == "local_dictionary" and c["label"] is None for c in codes)
    sources = plan["query_expansion"]["term_sources"]
    assert sources[concept["english_terms"][0]] == "dictionary:" + EXAMPLE.name


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


async def test_llm_plan_needs_no_client_translation_and_labels_its_terms(monkeypatch, service):
    proposed = {"queries": ["fibromyalgia"], "synonyms": ["fibromyalgia syndrome"]}

    async def complete(*args):
        return proposed

    monkeypatch.setattr("ema_rwe.service.complete_json", complete)
    plan = await service.plan_study_search("線維筋痛症の研究", use_llm=True)
    assert plan["status"] == "planned" and plan["client_expansion"]["required"] is False
    assert plan["query_expansion"]["term_sources"][proposed["synonyms"][0]] == "llm"


def test_dictionary_codes_report_their_file(tmp_path, monkeypatch):
    (tmp_path / EXAMPLE.name).write_bytes(EXAMPLE.read_bytes())
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(tmp_path))
    concept = next(c for c in json.loads(EXAMPLE.read_text(encoding="utf-8")) if c["code_candidates"])
    sources = expand(concept["input_terms"][0])["term_sources"]
    assert sources[concept["code_candidates"][0]["code"]] == "dictionary:" + EXAMPLE.name


def test_dictionary_folder_skips_hidden_files_and_names_a_bad_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(tmp_path))
    (tmp_path / "._resource.json").write_bytes(b"\x00\x05binary")
    (tmp_path / "empty.json").write_text("[]", encoding="utf-8")
    assert expand("テスト")["clinical"]["dictionaries"] == ["empty.json"]  # empty but configured
    (tmp_path / "notes.json").write_text('{"not": "a dictionary"}', encoding="utf-8")
    with pytest.raises(RWEError, match="notes.json"):
        expand("テスト")


def test_catalogue_status_reports_a_missing_dictionary_path(tmp_path, monkeypatch, service):
    monkeypatch.setenv("EMA_TERMINOLOGY_PATH", str(tmp_path / "absent.json"))
    assert service.catalogue_status()["dictionaries"]["error"]["code"] == "TERMINOLOGY_CONFIG_ERROR"


def test_near_distance_grows_with_phrase_length():
    # Distance counts tokens between the first and last word, so an adjacent phrase must still fit.
    assert fts_match([["a", "b"]], None).endswith(", 3)")
    assert fts_match([list("abcde")], None).endswith(", 4)")
    assert fts_match([list("abcdef")], None).endswith(", 5)")


def test_six_word_term_matches_when_adjacent(service):
    phrase = "acute exacerbation of idiopathic pulmonary fibrosis"
    service.repo.upsert(study("1", title=f"Incidence of {phrase} in nintedanib users"))
    result = service.search_studies("zzqx", synonyms=[phrase], darwin_only=False)
    assert [r["study_id"] for r in result["results"]] == ["1"]


async def test_compare_keeps_a_long_query_variant_as_one_phrase(service):
    # Given one study about the term and one sharing only its common words ("drug", "disease")
    service.repo.upsert(study("1", title="Drug-induced interstitial lung disease after chemotherapy"))
    service.repo.upsert(study("2", title="Drug utilisation in chronic kidney disease"))
    term = "drug-induced interstitial lung disease"  # five content words
    # compare_protocols treats each variant as an explicit term
    result = await service.compare_protocols("q", [term])
    assert result["search"]["total_matches"] == 1
    assert [r["study"]["study_id"] for r in result["rows"]] == ["1"]
    # while search_studies still splits long free text so a sentence-style question finds candidates
    assert service.search_studies(term, darwin_only=False)["total_matches"] == 2


def test_group_distance_from_source_text_overrides_word_count():
    assert fts_match([(["risk", "fibrillation"], 7)], None) == 'NEAR("risk" "fibrillation", 7)'
    assert fts_match([(["a", "b"], 1)], None).endswith(", 3)")  # never below the default


@pytest.mark.parametrize(
    "term",
    [
        "risk of stroke in patients with atrial fibrillation",  # five content words, three stop words
        "Malignant neoplasm of bronchus and lung",  # an ICD-10 title with four content words
    ],
)
async def test_a_term_with_stop_words_matches_its_own_text(service, term):
    # The index keeps stop words, so they must not use up the NEAR distance.
    service.repo.upsert(study("1", title=term.capitalize()))
    assert (await service.compare_protocols("q", [term]))["search"]["total_matches"] == 1
    assert service.search_studies(term, darwin_only=False)["total_matches"] == 1


async def test_protocol_retrieval_keeps_catalogue_only_fields(service):
    # Given study 123 imported from the CSV with medicines, conditions, outcomes, objective and sources
    fields = {
        "exposures": ["(N02AA01) morphine"],
        "conditions": ["Pain"],
        "outcomes": "Falls",
        "objective": "Assess falls",
        "catalogue_data_sources": ["CPRD"],
    }
    service.repo.upsert(study("123", title="Opioid safety", **fields))
    # When its protocol is retrieved, which re-reads the detail pages that lack those fields
    await service.get_protocol("123")
    # Then the page refresh is recorded and the catalogue fields, and their search, survive
    kept = service.repo.get("123")
    assert kept.detail_checked_at
    assert {name: getattr(kept, name) for name in fields} == fields
    assert [r["study_id"] for r in service.repo.search("morphine", None, False, None)] == ["123"]


async def test_protocol_text_layer_is_recorded_and_survives_a_page_refresh(service):
    service.repo.upsert(study("123", title="Opioid safety", protocol_listed=True))
    result = await service.get_protocol("123")
    assert result["protocol"]["text_layer"] == "full"
    kept = service.repo.get("123")
    assert kept.protocol_text_layer == "full" and kept.protocol_listed is True


def test_text_layer_of_an_image_only_pdf_is_none():
    with pymupdf.open() as doc:
        doc.new_page()
        assert text_layer(doc.tobytes()) == "none"


def test_import_records_whether_the_export_lists_a_protocol(settings, tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(
        "Study ID,Official title and acronym,Study type,Protocol file(s),Protocol URL\n"
        "1,With protocol,Non-interventional study,protocol.pdf,\n"
        "2,Without protocol,Non-interventional study,,\n",
        encoding="utf-8",
    )
    repo = Repository(settings.db_path)
    import_csv(repo, path)
    assert (repo.get("1").protocol_listed, repo.get("2").protocol_listed) == (True, False)
