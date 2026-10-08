import json

import httpx
import pytest
from pydantic import ValidationError

from ema_rwe.domain import CodeCandidate, RWEError
from ema_rwe.drugs import SOURCE_URL, drug_expansion, refresh_dictionary
from ema_rwe.medicines import catalogue_atc, expand_medicine, same_medicine
from ema_rwe.pdf import Page, search_sections, sections
from ema_rwe.service import Service
from ema_rwe.storage import import_csv
from ema_rwe.terminology import inline_codes, labelled_phrases
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
        # The EMA record's ATC code is not offered as a catalogue code search (names join the sources)
        assert all(q["query"] != "B01AF02" for q in plan["code_searches"])
    finally:
        await service.close()


def test_catalogue_labels_keep_combinations_whole_and_skip_ambiguous_codes(drug_file):
    labels = catalogue_atc(
        [
            "(A10BD20) metformin and empagliflozin",
            "(N03A) ANTIEPILEPTICS",
            "(N03AG01) valproic acid",
            "Keppra",
        ]
    )
    assert labels == {
        "A10BD20": "metformin and empagliflozin",
        "N03A": "ANTIEPILEPTICS",
        "N03AG01": "valproic acid",
    }
    # A single ingredient never resolves to a combination
    assert expand_medicine("metformin", labels) is None
    # A typed 2nd-level code is also an ICD-10 category (N03 nephritic syndrome): not a medicine class
    assert expand_medicine("N03", labels) is None
    assert set(expand_medicine("N03A", labels)["queries"]) == {"ANTIEPILEPTICS", "valproic acid"}


def test_both_ema_and_catalogue_codes_are_used_when_they_differ(drug_file):
    # The EMA dictionary and the catalogue may carry different ATC versions for the same medicine
    found = expand_medicine("apixaban", {"B01AX99": "apixaban", "B01AX": "Other antithrombotics"})
    assert found["atc_codes"] == ["B01AF02", "B01AX99"]
    # Only classes the catalogue itself records become category terms (B01AF is unknown to it here)
    assert found["category_terms"] == {"B01AX": "catalogue_atc", "Other antithrombotics": "catalogue_atc"}


def test_class_expansion_skips_generic_leaf_names_and_lists_the_class_first(drug_file):
    labels = {"B01AF": "Direct factor Xa inhibitors", "B01AF30": "combinations", "B01AF07": "edoxaban"}
    names = list(expand_medicine("b01af", labels)["queries"])
    # Lowercase codes resolve; the class name comes first; 'combinations' alone is never a search name
    assert names[0] == "Direct factor Xa inhibitors"
    assert "combinations" not in names and {"apixaban", "rivaroxaban", "edoxaban"} <= set(names)


def test_typed_code_must_exist_in_a_known_source(drug_file):
    # A Read-style or ICD-10-CM code with the same shape as an ATC class is not an ATC code
    assert expand_medicine("C10E", {"N03A": "ANTIEPILEPTICS"}) is None


def test_combination_query_resolves_only_to_the_whole_ingredient_set(drug_file):
    # The EMA dictionary has sitagliptin alone (Januvia) and with metformin (Janumet)
    assert [m["product_name"] for m in drug_expansion("metformin + sitagliptin")["matches"]] == ["Janumet"]
    found = expand_medicine("metformin and sitagliptin", {"A10BH01": "sitagliptin"})
    assert found["atc_codes"] == ["A10BD07"] and "sitagliptin" not in found["queries"]
    # Catalogue combination labels match in any order; a single ingredient's label never does
    labels = {"A10BD20": "metformin and empagliflozin", "A10BK03": "empagliflozin"}
    assert expand_medicine("empagliflozin and metformin", labels)["atc_codes"] == ["A10BD20"]
    # A combination known to no source resolves to nothing rather than to its ingredients
    assert expand_medicine("sitagliptin and empagliflozin", labels) is None


def test_atc_code_is_never_a_cross_source_key(drug_file):
    # The EMA dictionary does not turn a code into names: its record may label the code differently
    assert expand("B01AF02")["clinical"]["english_terms"] == []
    # A code resolves to names only through the catalogue's own '(code) name' entries
    labels = {"B01AF02": "apixaban", "B01AF01": "rivaroxaban"}
    assert set(expand_medicine("B01AF02", labels)["category_terms"]) == {"B01AF"}
    # A class the catalogue records only through the drug's own code adds no category term
    assert expand_medicine("B01AF02", {"B01AF02": "apixaban"})["category_terms"] == {}
    # An EMA record whose code the catalogue gives to another medicine is not a class member
    found = expand_medicine("B01AF", {"B01AF02": "edoxaban", "B01AF01": "rivaroxaban"})
    assert "apixaban" not in found["queries"] and {"edoxaban", "rivaroxaban"} <= set(found["queries"])


def test_whole_term_matching_never_finds_a_name_inside_a_longer_one(drug_file):
    phrase = "apixaban-like anticoagulants"
    assert [m["product_name"] for m in drug_expansion(phrase)["matches"]] == ["Eliquis"]
    assert drug_expansion(phrase, whole_term=True)["matches"] == []
    assert [m["product_name"] for m in drug_expansion("Eliquis", whole_term=True)["matches"]] == ["Eliquis"]
    assert expand_medicine(phrase, {}) is None


def test_salt_name_finds_the_base_medicine_but_ema_codes_are_not_search_terms(drug_file):
    # A salt or ester name still finds the products of its base ingredient under whole-term matching
    assert [
        m["product_name"] for m in drug_expansion("apixaban hydrochloride", whole_term=True)["matches"]
    ] == ["Eliquis"]
    # The EMA record's ATC code is kept for PDF text search but never becomes a catalogue search term
    expansion = expand("apixaban", whole_term=True)
    assert "Eliquis" in expansion["clinical"]["english_terms"]
    assert any(c["code"] == "B01AF02" for c in expansion["clinical"]["code_candidates"])
    groups = labelled_phrases("apixaban", expansion, False)
    assert all("b01af02" not in [w.casefold() for w in group] for _, group in groups)
    # A code the caller passes keeps its origin and stays a catalogue search term
    passed = expand("apixaban", None, [CodeCandidate(system="ATC", code="B01AF02")], whole_term=True)
    groups = labelled_phrases("apixaban", passed, False)
    assert any("b01af02" in [w.casefold() for w in group] for _, group in groups)


def test_salted_ingredient_is_found_by_its_base_name_only_as_a_whole_term(tmp_path, monkeypatch):
    path = tmp_path / "salted.json"
    monkeypatch.setenv("EMA_DRUG_DICTIONARY_PATH", str(path))
    payload = {
        "meta": {},
        "data": [
            row("Pradaxa", "dabigatran etexilate", "B01AE07"),
            row("Tecfidera", "dimethyl fumarate", "N07XX09"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert [m["product_name"] for m in drug_expansion("dabigatran", whole_term=True)["matches"]] == [
        "Pradaxa"
    ]
    # The base name is never scanned inside free text, where a generic word could match
    assert drug_expansion("dimethyl ester study")["matches"] == []
    assert drug_expansion("dabigatran users")["matches"] == []


def test_one_word_product_name_with_a_bracketed_history_still_counts_for_text_matching(tmp_path, monkeypatch):
    from ema_rwe.medicines import find_medicines, known_medicine_names

    path = tmp_path / "products.json"
    monkeypatch.setenv("EMA_DRUG_DICTIONARY_PATH", str(path))
    payload = {
        "meta": {},
        "data": [
            row("Spikevax (previously COVID-19 Vaccine Moderna)", "elasomeran", "J07BN01"),
            row("COVID-19 Vaccine (inactivated, adjuvanted) Valneva", "covid vaccine x", "J07BN03"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    names = known_medicine_names({})
    assert find_medicines("Safety of Spikevax in pregnancy", names) == ["elasomeran"]
    assert find_medicines("Safety of COVID-19 vaccine in pregnancy", names) == []


def test_catalogue_code_joins_only_the_whole_ingredient_set(drug_file):
    # A catalogue that labels the Janumet code with one of its ingredients is not the same medicine
    assert same_medicine("metformin", ["sitagliptin", "metformin"]) is False
    assert same_medicine("sitagliptin and metformin", ["metformin"]) is False
    found = expand_medicine("Janumet", {"A10BD07": "metformin"})
    assert found is None or "metformin" not in found["queries"]
    # Salt words and ingredient order still do not matter
    assert same_medicine("Metformin hydrochloride / sitagliptin phosphate", ["sitagliptin", "metformin"])
    label = "sitagliptin phosphate and metformin hydrochloride"
    assert label in expand_medicine("Janumet", {"A10BD07": label})["queries"]


def test_names_correspond_by_whole_words_never_by_letters(drug_file):
    # A prodrug or conjugate named with a prefix is another medicine, though its name contains the other's
    assert same_medicine("aprepitant", ["fosaprepitant dimeglumine"]) is False
    assert same_medicine("interferon beta-1a", ["peginterferon beta-1a"]) is False
    assert same_medicine("ethanol", ["methanol"]) is False
    # Qualifiers, word order and different salts of one moiety still correspond
    assert same_medicine("insulin (human)", ["human insulin (rDNA)"])
    assert same_medicine("botulinum toxin", ["botulinum toxin type A"])
    assert same_medicine("esomeprazole magnesium", ["esomeprazole sodium"])
    hpv = "papillomavirus (human types 16, 18)"
    assert same_medicine(hpv, ["human papillomavirus vaccine [types 16, 18]"])
    # Numbers given by both names identify the type or valency and must agree; a broader name may omit them
    assert same_medicine(hpv, ["human papillomavirus vaccine [types 6, 11, 16, 18]"]) is False
    assert same_medicine(
        "influenza, live attenuated", ["pandemic influenza vaccine (H5N1) (live attenuated)"]
    )


def test_separators_inside_brackets_do_not_split_a_medicine_name():
    from ema_rwe.drugs import ingredient_set

    assert ingredient_set("papillomavirus (human types 16, 18)") == {"papillomavirus human types 16 18"}
    assert ingredient_set("Icandra (previously Vildagliptin / metformin Novartis)") == {
        "icandra previously vildagliptin metformin novartis"
    }
    assert ingredient_set("empagliflozin and metformin") == {"empagliflozin", "metformin"}
    assert ingredient_set("a (x, (y, z)) / b") == {"a x y z", "b"}


async def test_refresh_reports_unreviewed_salt_word_candidates(tmp_path, monkeypatch):
    path = tmp_path / "salts.json"
    monkeypatch.setenv("EMA_DRUG_DICTIONARY_PATH", str(path))
    payload = {
        "meta": {},
        "data": [
            row("Yselty", "linzagolix choline", "H01CC04"),
            row("Generic linzagolix", "linzagolix", "H01CC04"),
            row("Orepaxam", "treprostinil diolamine", "B01AC21"),
            row("Remodulin", "treprostinil", "B01AC21"),
            row("Viread", "tenofovir disoproxil", "J05AF07"),
            row("Generic tenofovir", "tenofovir", "J05AF07"),
            row("Futuremab", "futuremab xyzate", "L01XX99"),
            row("Futuremab base", "futuremab", "L01XX99"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = await refresh_dictionary(False)
    # Reviewed counter-ions and reviewed non-salts are not reported; an unknown trailing word is
    assert result["cached"] and result["salt_word_candidates"] == ["xyzate"]
    # A counter-ion only adds the base-name lookup; a reviewed product-line word does not
    assert {m["product_name"] for m in drug_expansion("linzagolix", whole_term=True)["matches"]} == {
        "Generic linzagolix",
        "Yselty",
    }
    assert "Viread" not in [
        m["product_name"] for m in drug_expansion("tenofovir", whole_term=True)["matches"]
    ]


@pytest.fixture
def relation_file(tmp_path, monkeypatch):
    """A dictionary with a strain vaccine, a prodrug sharing its active form's code, renamed products."""
    path = tmp_path / "relations.json"
    monkeypatch.setenv("EMA_DRUG_DICTIONARY_PATH", str(path))
    payload = {
        "meta": {"timestamp": "2026-10-08"},
        "data": [
            row("Pandemrix", "pandemic influenza vaccine (H5N1) (live attenuated, nasal)", "J07BB03"),
            row("Fluenz", "influenza vaccine (live attenuated, nasal)", "J07BB03"),
            row("Emend", "aprepitant", "A04AD12"),
            row("Ivemend", "fosaprepitant", "A04AD12"),
            row(
                "Icandra (previously Vildagliptin / metformin Novartis)", "vildagliptin;metformin", "A10BD08"
            ),
            row("Twin (previously Alpha)", "alphamab", "L01XX01"),
            row("Twin (previously Beta)", "betamab", "L01XX02"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_broader_catalogue_name_is_a_category_term_never_a_synonym(relation_file):
    from ema_rwe.medicines import medicine_relation

    laiv = "pandemic influenza vaccine (H5N1) (live attenuated, nasal)"
    assert medicine_relation("influenza, live attenuated", [laiv]) == "broader"
    assert (
        medicine_relation(
            "influenza vaccine (H5N1) (live attenuated)", ["influenza vaccine (live attenuated)"]
        )
        == "narrower"
    )
    assert medicine_relation("insulin (human)", ["human insulin (rDNA)"]) == "equivalent"
    labels = {"J07BB03": "influenza, live attenuated", "J07BB01": "influenza, inactivated, whole virus"}
    found = expand_medicine(laiv, labels)
    assert "influenza, live attenuated" not in found["queries"]
    assert "influenza, live attenuated" in found["category_terms"]
    # The same catalogue name is the specific term for the unstrained product
    assert "influenza, live attenuated" in expand_medicine("Fluenz", labels)["queries"]


def test_a_class_lists_the_members_it_leaves_out(relation_file):
    found = expand_medicine("A04AD", {"A04AD12": "aprepitant", "A04AD01": "scopolamine"})
    assert "aprepitant" in found["queries"] and "fosaprepitant" not in found["queries"]
    assert found["omitted_members"] == [
        {
            "name": "fosaprepitant",
            "atc_code": "A04AD12",
            "catalogue_name": "aprepitant",
            "reason": "the catalogue gives this code to another medicine; not added as a member",
        }
    ]
    assert found["omitted_members_total"] == 1
    # The prodrug is still never a synonym of its active form
    assert expand_medicine("fosaprepitant", {"A04AD12": "aprepitant"}) is None


def test_a_current_product_name_finds_a_renamed_product_unless_it_is_ambiguous(relation_file):
    matches = drug_expansion("Icandra", whole_term=True)["matches"]
    assert [m["product_name"] for m in matches] == ["Icandra (previously Vildagliptin / metformin Novartis)"]
    assert expand_medicine("Icandra", {"A10BD08": "metformin and vildagliptin"})["atc_codes"] == ["A10BD08"]
    # Two renamed products now share a name but not their ingredients: neither is chosen
    assert drug_expansion("Twin", whole_term=True)["matches"] == []
    # The alias is a whole-name key only, never found inside free text
    assert drug_expansion("Icandra-like combinations")["matches"] == []
