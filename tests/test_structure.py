"""Section structure: roles, reading and evidence acceptance are separate decisions (structural-v5)."""

import pymupdf
import pytest

from ema_rwe.domain import DataSource, Evidence, Extraction, Fact, RWEError
from ema_rwe.pdf import extract_pages, heading_role, reading_order, sections, validate_evidence


def pdf(*texts: str, toc=None) -> bytes:
    with pymupdf.open() as doc:
        for text in texts:
            doc.new_page().insert_text((40, 40), text, fontsize=8)
        if toc:
            doc.set_toc(toc)
        return doc.tobytes()


def chunk_with(chunks, words: str) -> dict:
    return next(c for c in chunks if words in c["text"])


def fact(quote: str, page: int) -> Fact:
    return Fact(value=quote[:40], evidence=[Evidence(page=page, quote=quote)])


METHODS = (
    "9. RESEARCH METHODS\n9.1 Study design\nA retrospective cohort study of new users with outcome follow-up."
)


def test_conduct_chapter_keeps_study_specific_definitions_readable_and_quotable():
    # Given an adverse-event chapter whose subsection defines the events the study records
    definition = (
        "Adverse event (AE): Any untoward medical occurrence in a participant administered a product."
    )
    pages = extract_pages(
        pdf(METHODS, f"11. MANAGEMENT AND REPORTING OF ADVERSE EVENTS\n11.1 Definitions\n{definition}")
    )
    chunks = sections(pages)
    # Then the subsection inherits the low-priority conduct role, is read, and its quote is accepted
    section = chunk_with(chunks, "untoward")
    assert section["role"] == "conduct" and section["role_basis"] == "parent"
    assert section in reading_order(chunks)
    validate_evidence(Extraction(outcomes=[fact(definition, 2)]), pages, chunks)


def test_administrative_quote_is_accepted_but_reference_quote_is_rejected():
    # Given the same data source named in a milestones chapter and in the reference list
    milestone = "Data will be extracted from the Example Claims Database in 2025."
    reference = "Smith et al. described the Example Claims Database in 2019."
    pages = extract_pages(pdf(METHODS, f"6. MILESTONES\n{milestone}", f"13. REFERENCES\n{reference}"))
    chunks = sections(pages)
    assert chunk_with(chunks, "extracted")["role"] == "administrative"
    assert chunk_with(chunks, "extracted") not in reading_order(chunks)  # still not read by default
    # Then a verbatim quote from the administrative chapter is accepted (its role only lowers priority)
    source = DataSource(
        value="Example Claims Database", usage="planned", evidence=[Evidence(page=2, quote=milestone)]
    )
    validate_evidence(Extraction(data_sources=[source]), pages, chunks)
    # But a quote found only in the references is not this study's method
    cited = DataSource(
        value="Example Claims Database", usage="planned", evidence=[Evidence(page=3, quote=reference)]
    )
    with pytest.raises(RWEError, match="background, references, contents or checklist"):
        validate_evidence(Extraction(data_sources=[cited]), pages, chunks)


def test_amendment_chapter_is_read_under_its_own_role():
    change = "Sensitivity analysis: remove 6-month washout period."
    chunks = sections(extract_pages(pdf(METHODS, f"4. AMENDMENTS AND UPDATES\n{change}")))
    section = chunk_with(chunks, "washout")
    assert section["role"] == "amendments" and section["relevant"]


def test_template_chapter_titles_have_roles():
    assert heading_role("7. Research Question and Objectives") == "objectives"
    assert heading_role("List of Tables") == "contents"
    assert heading_role("10 Protection of Human Subjects") == "conduct"
    assert heading_role("12 Plans for Disseminating and Communicating Study Results") == "conduct"
    assert heading_role("3. Responsible Parties") == "administrative"


def test_role_basis_records_where_each_role_came_from():
    pages = extract_pages(
        pdf(
            METHODS,
            "Patients with a prior event are excluded from the cohort at baseline.",
            toc=[[1, "9.2 Study population", 2]],
        )
    )
    by_page = {c["page"]: c for c in sections(pages)}
    assert by_page[1]["role_basis"] == "heading"
    assert by_page[2]["role"] == "population" and by_page[2]["role_basis"] == "bookmark"
