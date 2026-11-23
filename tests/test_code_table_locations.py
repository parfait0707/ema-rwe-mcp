"""Code table location rows and the section map (spec v1.33): built from the parse alone, stored with the
study's protocol observation and returned by find_facts as item kinds code_table and section_map."""

import pytest
from test_core import sample_analysis

from ema_rwe import evidence
from ema_rwe.pdf import Page, audit_extraction, code_systems, normalize_quote, protocol_locations
from ema_rwe.service import Service

ANNEX = "12 Annexes"


@pytest.fixture
async def analysed(service):
    """Study 123 extracted locally (sample_analysis) with its PDF kept."""
    first = await service.analyze_protocol("123")
    await service.cache_protocol_analysis("123", first["source"]["fingerprint"], sample_analysis(), True)
    return service


def annex_page(number: int, title: str, text: str) -> Page:
    path = f"{ANNEX} / {title}"
    return Page(number, text, chapter=title, chapter_top=ANNEX, chapter_path=path)


def codes(first: int, count: int = 10) -> str:
    """count ICD-10 code lines, E10.0 upward from E{first}."""
    return "\n".join(f"E{first + i // 10}.{i % 10}    Diabetes subtype {i}" for i in range(count))


def body_page(number: int, text: str) -> Page:
    return Page(number, text, chapter="9 Methods", chapter_top="9 Methods", chapter_path="9 Methods")


def test_a_code_table_over_many_pages_is_one_row_without_a_page_limit():
    title = "12.1 Annex 1 Diabetes ICD-10 codes"
    pages = [body_page(1, "9 Methods\nThe cohort is described in the annex.")]
    pages += [annex_page(n, title, (f"{title}\n" if n == 2 else "") + codes(10 + n)) for n in range(2, 16)]
    pages.append(
        annex_page(16, "12.2 Annex 2 Study milestones", "12.2 Annex 2 Study milestones\nStart 2024.")
    )
    tables = protocol_locations(pages)["code_tables"]
    assert [(t["value"], t["page"], t["end_page"], t["kind"]) for t in tables] == [
        (f"{ANNEX} / {title}", 2, 15, "codes")
    ]
    assert tables[0]["systems"] == ["ICD-10"] and tables[0]["systems_inferred"] == []


def test_a_new_heading_starts_a_new_row():
    pages = [
        annex_page(1, "12.1 Asthma", "12.1 Asthma\n" + codes(40)),
        annex_page(2, "12.2 Obesity", "12.2 Obesity\n" + codes(66)),
    ]
    tables = protocol_locations(pages)["code_tables"]
    assert [(t["value"].split(" / ")[-1], t["page"], t["end_page"]) for t in tables] == [
        ("12.1 Asthma", 1, 1),
        ("12.2 Obesity", 2, 2),
    ]


def test_a_term_list_appendix_without_numeric_codes_is_a_terms_row():
    title = "Annex 3 MedDRA preferred terms"
    terms = "\n".join(["Pancreatic carcinoma", "Pancreatic carcinoma metastatic", "Pancreatic neoplasm"] * 3)
    pages = [annex_page(1, title, f"{title}\n{terms}"), annex_page(2, title, terms)]
    (table,) = protocol_locations(pages)["code_tables"]
    assert (table["kind"], table["page"], table["end_page"]) == ("terms", 1, 2)
    assert table["systems"] == ["MedDRA"]


def test_a_table_titled_as_codes_is_a_row_even_with_few_codes():
    title = "12.4 Hypertension ICD-10-CM Codes"
    pages = [annex_page(1, title, f"{title}\nI10 Essential hypertension\nI11.9 Hypertensive heart disease")]
    (table,) = protocol_locations(pages)["code_tables"]
    assert (table["kind"], table["page"]) == ("codes", 1)


def test_a_few_codes_in_running_text_are_not_a_code_table():
    pages = [body_page(1, "9 Methods\nKidney failure is ICD-10 N18.5; diabetes is E11 or E11.9.")]
    assert protocol_locations(pages)["code_tables"] == []


def test_a_title_printed_a_page_before_its_bookmark_joins_the_bookmarks_row():
    parent = f"{ANNEX} / 16.4.1.3 Chronic lung disease"
    child = f"{parent} / 16.4.1.3.1 ICD-10-CM Codes"
    pages = [
        Page(1, "16.4.1.3.1 ICD-10-CM Codes\n" + codes(84), "16.4.1.3 Chronic lung disease", ANNEX, parent),
        Page(2, codes(27), "16.4.1.3.1 ICD-10-CM Codes", ANNEX, child),
    ]
    (table,) = protocol_locations(pages)["code_tables"]
    assert (table["value"], table["page"], table["end_page"]) == (child, 1, 2)


def test_the_quote_is_a_verbatim_line_of_the_first_page():
    title = "12.1 Annex 1 Diabetes ICD-10 codes"
    pages = [annex_page(1, title, f"{title}\n" + codes(10))]
    locations = protocol_locations(pages)
    for row in locations["code_tables"] + locations["section_map"]:
        assert row["quote"] and normalize_quote(row["quote"]) in normalize_quote(pages[row["page"] - 1].text)


def test_the_section_map_keeps_method_chapters_and_leaves_out_references():
    pages = [
        Page(1, "9 Methods\nNew users.", "9 Methods", "9 Methods", "9 Methods"),
        Page(2, "More methods.", "9 Methods", "9 Methods", "9 Methods"),
        Page(3, "13 References\nSmith J et al. 2019.", "13 References", "13 References", "13 References"),
    ]
    chapters = protocol_locations(pages)["section_map"]
    assert [(c["value"], c["role"], c["page"], c["end_page"]) for c in chapters] == [
        ("9 Methods", "methods", 1, 2)
    ]


@pytest.mark.parametrize(
    ("heading", "text", "named", "inferred"),
    [
        ("Annex ICD-10 codes", "C25 C25.0", ["ICD-10"], []),
        ("Annex codes", "C25 C25.0 C25.1", [], ["ICD-10"]),  # a letter code may also be OPCS-4
        ("OPCS-4 procedure codes", "K40.1 K40.2", ["OPCS-4"], []),  # named: the shape adds no ICD-10
        ("Annex codes", "250.00 250.01", [], ["ICD-9"]),
        ("Drug list", "A10BA02 metformin", ["ATC"], []),  # the full ATC shape is ATC's own
        ("Annex", "Read codes C10E. and C10F.", ["Read"], []),
        ("Annex", "ICD-11 5A11", ["ICD-11"], []),
        ("Annex", "SNOMED CT 44054006", ["SNOMED CT"], []),
    ],
)
def test_coding_systems_prefer_named_ones_and_mark_shape_guesses(heading, text, named, inferred):
    assert code_systems(heading, text) == (named, inferred)


def test_pages_a_code_table_row_covers_are_not_reported_as_uncited():
    title = "12.1 Annex 1 Diabetes ICD-10 codes"
    pages = [annex_page(1, title, f"{title}\n" + codes(10))]
    assert not any("Pages listing codes" in n for n in audit_extraction(sample_analysis(), pages))


async def test_find_facts_returns_location_rows_only_when_asked_and_stores_them(analysed, monkeypatch):
    """The rows are built once from the kept PDF and stored with the observation, then reused."""
    analysed.repo.observe("123", locations=None)  # as a database from before v1.33
    builds = []
    real = evidence.protocol_locations
    monkeypatch.setattr(evidence, "protocol_locations", lambda pages: builds.append(1) or real(pages))

    plain = analysed.find_facts(study_ids=["123"])
    assert not {"code_table", "section_map"} & {f["item_kind"] for f in plain["facts"]}
    assert "no_locations" not in plain and builds == []

    found = analysed.find_facts(study_ids=["123"], item_kinds=["section_map"])
    rows = found["facts"]
    assert rows and {r["item_kind"] for r in rows} == {"section_map"} and found["no_locations"] == []
    assert {r["status"] for r in rows} == {"verified_local"} and builds == [1]
    design = next(r for r in rows if r["value"] == "8.1 Study design")
    assert design["evidence"] == [{"page": 1, "section": "8.1 Study design", "quote": "8.1 Study design"}]
    assert design["pdf_sha256"] == design["protocol_id"].rsplit("_", 1)[1]
    assert analysed.repo.observation("123")["locations"]["protocol_id"] == design["protocol_id"]

    again = analysed.find_facts(study_ids=["123"], item_kinds=["section_map"], text="data sources")
    assert [r["value"] for r in again["facts"]] == ["8.2 Data sources"] and builds == [1]


async def test_wider_scopes_use_stored_locations_only(analysed, monkeypatch):
    analysed.repo.observe("123", locations=None)
    monkeypatch.setattr(evidence, "protocol_locations", lambda pages: pytest.fail("built in a wide scope"))
    found = analysed.find_facts(item_kinds=["code_table"])
    assert found["no_locations"] == ["123"] and found["facts"] == []


def test_a_study_without_a_kept_pdf_has_no_locations(settings):
    service = Service(settings)
    service.repo.observe("999", protocol_id="pdf_999_" + "0" * 64)
    found = service.find_facts(study_ids=["999"], item_kinds=["code_table"])
    assert found["no_locations"] == ["999"] and found["facts"] == []
    assert "locations" not in service.repo.observation("999")


async def test_retrieving_a_protocol_stores_its_locations(service):
    result = await service.get_protocol("123")
    stored = service.repo.observation("123")["locations"]
    assert stored["protocol_id"] == result["protocol"]["protocol_id"]
    assert stored["code_tables"] == [] and stored["section_map"]
