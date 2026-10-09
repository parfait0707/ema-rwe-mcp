"""Catalogue screening: co-occurrence matching, role-scoped columns, listing and explicit choice."""

import csv
import io
import json
from pathlib import Path

import pymupdf
import pytest
from test_comparison import extend_website, seed
from test_drugs import drug_file  # noqa: F401  (fixture)

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
    path.write_text(stream.getvalue(), encoding="utf-8-sig", newline="")
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
    # A forced page refresh rebuilds the study from the detail pages; observations live apart from it
    await service.get_study("123", refresh=True)
    assert service.repo.get("123").protocol_listed is True
    observed = service.repo.observation("123")
    assert observed["text_layer"] == "full" and observed["protocol_found"] is True


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


def test_schema_5_text_layer_moves_to_the_observations(settings):
    import sqlite3

    repo = Repository(settings.db_path)
    repo.upsert(study("1", title="Old"))
    with sqlite3.connect(settings.db_path) as db:  # a version-5 database kept the layer in the study body
        body = json.loads(db.execute("SELECT body FROM studies").fetchone()[0])
        db.execute("UPDATE studies SET body=?", (json.dumps({**body, "protocol_text_layer": "none"}),))
        db.execute("DROP TABLE protocol_observations")
        db.execute("PRAGMA user_version=5")
    migrated = Repository(settings.db_path)
    assert migrated.get("1").title == "Old"
    assert migrated.observation("1") == {"text_layer": "none"}


def test_pass_table_medicines_keep_known_names_codes_and_page_only():
    from ema_rwe.medicines import pass_table_medicines
    from ema_rwe.pdf import Page

    names = {"duloxetine": "duloxetine", "cymbalta": "duloxetine", "venlafaxine": "venlafaxine"}
    pages = [
        Page(1, "Title page"),
        Page(
            2,
            "Active substance   ATC code N06AX21, duloxetine\nMedicinal product(s): Cymbalta, Xeristar\n"
            "Product reference: LY248686\nResearch question and objectives: compared with venlafaxine",
        ),
    ]
    # The comparator in another field is not taken; free text such as product codes is never kept
    assert pass_table_medicines(pages, names) == [
        {"term": "duloxetine", "atc_codes": ["N06AX21"], "page": 2, "source": "protocol_pass_table"}
    ]


@pytest.mark.usefixtures("drug_file")
async def test_backfill_fills_medicines_from_text_then_protocols_and_stops_on_rate_limit(
    service, monkeypatch
):
    service.repo.upsert(study("1", title="Apixaban users in primary care", protocol_listed=False))
    service.repo.upsert(study("123", title="Opioid safety", protocol_listed=True))
    service.repo.upsert(study("2", title="Anticoagulant cohort", protocol_listed=True))
    service.repo.upsert(
        study("3", title="Statin", exposures=["(C10AA05) atorvastatin"], protocol_listed=True)
    )
    summary = await service.backfill_protocols(interval=0, download=False)
    # Text step: a named medicine becomes an observed exposure, searchable in the exposure column
    assert summary["named_in_text"] == 1
    assert service.repo.observation("1")["exposures"] == [{"term": "apixaban", "source": "catalogue_text"}]
    rows = service.repo.search("apixaban", None, False, None, role="exposure")
    assert [r["study_id"] for r in rows] == ["1"]
    # Download step: only studies without medicines whose export lists a protocol; stops at a rate limit
    calls = []
    real = service.get_protocol

    async def get_protocol(study_id, *args, **kwargs):
        calls.append(study_id)
        if study_id == "2":
            raise RWEError("EMA_RATE_LIMITED", "HTTP 429")
        return await real(study_id, *args, **kwargs)

    monkeypatch.setattr(service, "get_protocol", get_protocol)
    summary = await service.backfill_protocols(interval=0)
    assert calls == ["123", "2"] and summary["stopped"].startswith("EMA_RATE_LIMITED")
    assert service.repo.observation("123")["protocol_found"] is True
    # A rerun resumes: the study already tried is skipped
    calls.clear()
    await service.backfill_protocols(interval=0, limit=1)
    assert calls == ["2"]


@pytest.mark.usefixtures("drug_file")
async def test_backfill_retries_a_study_interrupted_after_its_documents_page(service, monkeypatch):
    service.repo.upsert(study("123", title="Opioid safety", protocol_listed=True))
    real_load = service.archive.load

    def outage(protocol_id):  # the documents page was read (protocol_found recorded), the PDF was not
        raise RWEError("EMA_UNAVAILABLE", "connection reset")

    monkeypatch.setattr(service.archive, "load", outage)
    summary = await service.backfill_protocols(interval=0)
    # An outage stops the run like a rate limit, and leaves the study to the next run
    assert summary["stopped"].startswith("EMA_UNAVAILABLE")
    assert service.repo.observation("123")["protocol_found"] is True
    assert not service.repo.observation("123").get("backfill_done")
    monkeypatch.setattr(service.archive, "load", real_load)
    summary = await service.backfill_protocols(interval=0)
    assert summary["tried"] == 1 and service.repo.observation("123")["backfill_done"] is True


def test_atc_codes_go_to_the_medicine_written_just_before_them():
    from ema_rwe.medicines import pass_table_medicines
    from ema_rwe.pdf import Page

    names = {
        "dulaglutide": "dulaglutide",
        "liraglutide": "liraglutide",
        "abiraterone acetate": "abiraterone acetate",
        "enzalutamide": "enzalutamide",
        "docetaxel": "docetaxel",
    }
    page = Page(
        1,
        "Active substance  Dulaglutide (A10BJ05), Liraglutide (A10BJ02)\nProduct reference  N/A\n"
        "Medicinal product  abiraterone acetate (ATC code L02BX03); enzalutamide (ATC code L02BB04), docetaxel\n",
    )
    codes = {m["term"]: m["atc_codes"] for m in pass_table_medicines([page], names)}
    assert codes == {
        "dulaglutide": ["A10BJ05"],
        "liraglutide": ["A10BJ02"],
        "abiraterone acetate": ["L02BX03"],
        "enzalutamide": ["L02BB04"],
        "docetaxel": [],
    }


@pytest.mark.usefixtures("drug_file")
async def test_backfill_records_a_permanent_http_error_as_done(service, monkeypatch):
    service.repo.upsert(study("123", title="Opioid safety", protocol_listed=True))

    async def gone(study_id, *args, **kwargs):
        raise RWEError("EMA_HTTP_ERROR", "EMA returned HTTP 404.")

    monkeypatch.setattr(service, "get_protocol", gone)
    summary = await service.backfill_protocols(interval=0)
    assert summary["stopped"] is None and summary["errors"] == {"EMA_HTTP_ERROR": 1}
    assert service.repo.observation("123")["backfill_done"] is True


@pytest.mark.usefixtures("drug_file")
async def test_text_matches_step_aside_once_the_catalogue_lists_medicines(service):
    service.repo.upsert(study("1", title="Apixaban users"))
    await service.backfill_protocols(interval=0, download=False)
    assert [r["study_id"] for r in service.repo.search("apixaban", None, False, None, role="exposure")] == [
        "1"
    ]
    # A newer export fills the medicines: the text match no longer counts, even before a rerun
    service.repo.upsert(study("1", title="Apixaban users", exposures=["(B01AF01) rivaroxaban"]))
    row = service.repo.search("rivaroxaban", None, False, None)[0]
    assert row["observed_exposures"] == []


def test_pass_table_keeps_classes_and_written_codes_but_no_free_text():
    from ema_rwe.medicines import known_class_names, pass_table_medicines
    from ema_rwe.pdf import Page

    labels = {
        "C03": "DIURETICS",
        "C03AA": "Thiazides, plain",
        "C07": "BETA BLOCKING AGENTS",
        "N05A": "ANTIPSYCHOTICS",
    }
    names, classes = {"vizamyl": "flutemetamol (18F)"}, known_class_names(labels)

    def fields(substance, product="Not applicable"):
        page = Page(
            2, f"Active substance: {substance}\nMedicinal product: {product}\nProduct reference: N/A\n"
        )
        return [(m["term"], m["atc_codes"]) for m in pass_table_medicines([page], names, classes, labels)]

    # Class names with the codes written after them; a trademark sign does not hide a product name
    assert fields("Drug class ATC code Diuretics C03 Beta blocking agents C07") == [
        ("DIURETICS", ["C03"]),
        ("BETA BLOCKING AGENTS", ["C07"]),
    ]
    assert fields("Radionuclide imaging", "VIZAMYL™") == [("flutemetamol (18F)", [])]
    # An unknown medicine is kept by its written code, never by its free-text name
    assert fields("Loperamide (INN common name) Pharmacotherapeutic group ATC code: A07DA03") == [
        ("A07DA03", ["A07DA03"])
    ]
    # Nothing to record: 'not applicable', a code-shaped 2nd-level token without ATC context
    assert fields("Not applicable") == []
    assert fields("Patients with C03 coded events") == []


def test_text_medicines_skip_study_acronyms_and_measured_substances():
    from ema_rwe.medicines import find_medicines

    names = {"sonata": "zaleplon", "nitric oxide": "nitric oxide", "letrozole": "letrozole"}
    text = "Early breast cancer in Greece (SONATA study). SONATA is a research collaboration on letrozole."
    assert find_medicines(text, names) == ["letrozole"]
    assert find_medicines("Value of fractional exhaled Nitric Oxide in asthma", names) == []
    assert find_medicines("Serum letrozole levels", names) == []
    assert find_medicines("Inhaled nitric oxide in preterm infants", names) == ["nitric oxide"]


def test_pass_table_reads_code_first_fields_split_codes_and_receptor_classes():
    from ema_rwe.medicines import pass_table_medicines
    from ema_rwe.pdf import Page

    names = {
        "enzalutamide": "enzalutamide",
        "fosphenytoin": "fosphenytoin",
        "angiotensin ii": "angiotensin II",
    }
    labels = {"C09C": "ANGIOTENSIN II RECEPTOR BLOCKERS (ARBs), PLAIN", "B05": "BLOOD SUBSTITUTES"}

    def fields(substance):
        page = Page(1, f"Active substance: {substance}\nProduct reference: N/A\n")
        return [(m["term"], m["atc_codes"]) for m in pass_table_medicines([page], names, {}, labels)]

    # A field that writes the code first gives each code to the name after it, never to an earlier one
    assert fields("L02BB04 (enzalutamide) L02BX03 (abiraterone)") == [
        ("enzalutamide", ["L02BB04"]),
        ("L02BX03", ["L02BX03"]),
    ]
    # 'N03A B05' is N03AB05 broken across a line, not the 2nd-level code B05
    assert fields("Fosphenytoin sodium ATC code: N03A B05") == [("fosphenytoin", ["N03AB05"])]
    # 'Angiotensin II receptor blocker' is a class, not the medicine angiotensin II
    assert fields("Angiotensin II receptor blocker (ARB) - ATC C09C") == [
        ("ANGIOTENSIN II RECEPTOR BLOCKERS (ARBs), PLAIN", ["C09C"])
    ]


def test_pass_table_codes_never_cross_neighbours_or_take_icd_or_variant_codes():
    from ema_rwe.medicines import known_class_names, pass_table_medicines
    from ema_rwe.pdf import Page

    names = {
        "enzalutamide": "enzalutamide",
        "pembrolizumab": "pembrolizumab",
        "nivolumab": "nivolumab",
        "tofacitinib": "tofacitinib",
        "sotorasib": "sotorasib",
        "heparin": "heparin",
    }
    labels = {
        "M05BA": "Bisphosphonates",
        "J06BA": "Immunoglobulins, normal human",
        "B05BA10": "combinations",
        "B01AC": "Platelet aggregation inhibitors excl. heparin",
        "C10AA": "HMG CoA reductase inhibitors",
    }

    def fields(substance):
        page = Page(1, f"Active substance: {substance}\nProduct reference: N/A\n")
        found = pass_table_medicines([page], names, known_class_names(labels), labels)
        return [(m["term"], m["atc_codes"]) for m in found]

    # An unknown medicine's code stays its own, whichever side the names are written on
    assert fields("L02BX03 (abiraterone) L02BB04 (enzalutamide)") == [
        ("enzalutamide", ["L02BB04"]),
        ("L02BX03", ["L02BX03"]),
    ]
    assert fields("enzalutamide (L02BB04), abiraterone (L02BX03)") == [
        ("enzalutamide", ["L02BB04"]),
        ("L02BX03", ["L02BX03"]),
    ]
    # 'INN code (brand)' with unknown brands is still 'name, code'
    assert fields("Pembrolizumab L01FF02 (Brand X), nivolumab L01FF01 (Brand Y)") == [
        ("pembrolizumab", ["L01FF02"]),
        ("nivolumab", ["L01FF01"]),
    ]
    # ICD-10 categories with no catalogue name and no 'ATC' context, and a gene variant, are no codes
    assert fields("tofacitinib for rheumatoid arthritis (M05) and J06 infections") == [("tofacitinib", [])]
    assert fields("sotorasib for KRAS G12C mutated NSCLC") == [("sotorasib", [])]
    # Codes listed with commas are not joined into another code
    assert [c for _, c in fields("ATC codes: B01A, C10")] == [["B01A"], ["C10"]]
    # A generic leaf name is not a term; a name inside a class name is part of the class
    assert fields("B05BA10") == [("B05BA10", ["B05BA10"])]
    assert fields("Platelet aggregation inhibitors excl. heparin") == [
        ("Platelet aggregation inhibitors excl. heparin", [])
    ]


async def test_page_refresh_keeps_the_typed_export_source_tags(service):
    """The detail page's F8.7 values never replace the tags of an exported study."""
    # A study known only from its detail page takes the page's F8.7 values
    service.repo.upsert(study("123", title="Opioid safety"))
    await service.get_study("123", refresh=True)
    assert service.repo.get("123").data_source_types == ["Electronic healthcare records (EHR)"]
    # An exported study keeps the tags of the typed exports
    exported = {"metadata_source": "CSV SHA256:x", "data_source_types_source": "filtered export claims.csv"}
    service.repo.upsert(study("123", title="Opioid safety", data_source_types=["claims"], **exported))
    for _ in range(2):  # every later refresh too, not only the first
        await service.get_study("123", refresh=True)
        kept = service.repo.get("123")
        assert kept.data_source_types == ["claims"]
        assert kept.data_source_types_source == "filtered export claims.csv"
