"""PDF bookmark outline guides section roles and reading order; schema 0.3 cohort block round-trips."""

import pymupdf
from test_core import sample_analysis

from ema_rwe.comparison import cohort_cells
from ema_rwe.domain import Cohort, DesignSchema, Evidence, Extraction, Fact
from ema_rwe.llm import merge_extractions
from ema_rwe.pdf import (
    extract_pages,
    prune_unverifiable,
    reading_order,
    search_sections,
    sections,
    validate_evidence,
)


def pdf_with_bookmarks(with_toc=True) -> bytes:
    texts = [
        "Study protocol\nSome front matter without a numbered heading.",
        "The cohort is defined by a first dispensing (index date).\nExclusion: prior pancreatitis.",
        "Outcomes: acute pancreatitis coded K85. See Annex 3 for the code list.",
        "Dr Example, MD. Curriculum vitae. Publications 1999-2024.",
        "K85 Acute pancreatitis\nK85.0 Idiopathic acute pancreatitis",
        "Checklist question 1: Does the protocol describe missing data handling?",
    ]
    with pymupdf.open() as doc:
        for text in texts:
            doc.new_page().insert_text((50, 50), text, fontsize=9)
        if with_toc:
            doc.set_toc(
                [
                    [1, "9 Research methods", 2],
                    [2, "9.2 Study population", 2],
                    [2, "9.3 Outcomes", 3],
                    [1, "Annex 2 Curriculum vitae", 4],
                    [1, "Annex 3 Code list", 5],
                    [1, "Annex 4 ENCePP checklist", 6],
                ]
            )
        return doc.tobytes()


def test_bookmarks_label_pages_and_supply_roles():
    pages = extract_pages(pdf_with_bookmarks())
    assert pages[0].chapter is None and pages[1].chapter == "9.2 Study population"
    assert pages[2].chapter == "9.3 Outcomes" and pages[2].chapter_top == "9 Research methods"
    chunks = sections(pages)
    by_page = {c["page"]: c for c in chunks}
    assert by_page[2]["role"] == "population" and by_page[3]["role"] == "definitions"
    assert by_page[4]["role"] == "appendix" and by_page[4]["chapter"].startswith("Annex 2")


def test_reading_order_keeps_body_and_referenced_or_code_list_appendices_only():
    chunks = sections(extract_pages(pdf_with_bookmarks()))
    pages_read = sorted({c["page"] for c in reading_order(chunks)})
    assert 2 in pages_read and 3 in pages_read  # body chapters
    assert 5 in pages_read  # Annex 3: referenced from the body and a code list
    assert 4 not in pages_read  # curriculum vitae: unreferenced appendix
    assert 6 not in pages_read  # checklist appendix
    plain = sections(extract_pages(pdf_with_bookmarks(with_toc=False)))
    assert reading_order(plain) == [c for c in plain if c["relevant"]]  # legacy behaviour without bookmarks


def test_search_ranks_bookmark_chapter_matches_first():
    chunks = sections(extract_pages(pdf_with_bookmarks()))
    hits = search_sections(chunks, "outcomes")["results"]
    assert hits and hits[0]["chapter"] == "9.3 Outcomes"


def fact(text, page=1, section=None):
    return Fact(value=text, evidence=[Evidence(page=page, section=section, quote=text)])


def test_cohort_block_merges_validates_and_prunes(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    a = Extraction(
        cohort=Cohort(
            inclusion_criteria=[
                fact("A new-user active comparator cohort study of adults.", section="8.1 Study design")
            ],
            index_date=fact(
                "The study will use Example Primary Care Database (EPCD).", section="8.2 Data sources"
            ),
        )
    )
    b = Extraction(
        cohort=Cohort(
            exclusion_criteria=[
                fact(
                    "Diabetes is defined by two ICD-10 E11 diagnoses in 365 days.",
                    section="8.3 Disease definitions",
                )
            ],
            index_date=fact(
                "We will use propensity score weighting and Cox regression.", section="8.4 Analysis"
            ),
            design_schema=DesignSchema(
                figure_pages=[1], time_windows=[fact("in 365 days", section="8.3 Disease definitions")]
            ),
        )
    )
    merged = merge_extractions([a, b])
    assert merged.schema_version == "0.3"
    assert len(merged.cohort.inclusion_criteria) == 1 and len(merged.cohort.exclusion_criteria) == 1
    assert "\n" in merged.cohort.index_date.value and merged.cohort.design_schema.figure_pages == [1]
    validate_evidence(merged, pages)
    merged.cohort.exclusion_criteria.append(fact("this criterion is not in the pdf at all"))
    pruned, dropped = prune_unverifiable(merged, pages)
    assert dropped == 1 and len(pruned.cohort.exclusion_criteria) == 1
    assert pruned.cohort.design_schema.time_windows[0].evidence[0].section == "8.3 Disease definitions"


def test_cohort_cells_flatten_for_the_table():
    cohort = Cohort(
        inclusion_criteria=[fact("adults >= 18", page=3)], follow_up=fact("until death", page=4)
    ).model_dump()
    lines = cohort_cells(cohort)
    assert lines == ["組入: adults >= 18 (p. 3)", "追跡: until death (p. 4)"]
    assert cohort_cells(None) is None and sample_analysis().cohort is None


def test_drop_invalid_evidence_keeps_nested_cohort_block():
    from ema_rwe.llm import drop_invalid_evidence

    raw = {
        "cohort": {
            "inclusion_criteria": [
                {"value": "kept", "evidence": [{"page": 1, "quote": "a quote long enough"}]}
            ],
            "exclusion_criteria": [{"value": "gone", "evidence": [{"page": 1, "quote": "tiny"}]}],
            "index_date": None,
            "design_schema": {"figure_pages": [4], "time_windows": []},
        }
    }
    cleaned = drop_invalid_evidence(raw)
    assert cleaned["cohort"]["inclusion_criteria"][0]["value"] == "kept"
    assert cleaned["cohort"]["exclusion_criteria"] == []
    assert cleaned["cohort"]["design_schema"]["figure_pages"] == [4]
