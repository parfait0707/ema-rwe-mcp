"""Section structure: roles, reading and evidence acceptance are separate decisions (structural-v5)."""

import pymupdf
import pytest

from ema_rwe.domain import DataSource, Evidence, Extraction, Fact, RWEError
from ema_rwe.pdf import (
    extract_pages,
    heading_role,
    is_heading,
    reading_order,
    section_number,
    sections,
    validate_evidence,
)


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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("II.4 Abstract", True),  # roman multi-level
        ("III.13 References", True),
        ("I BACKGROUND AND PREAMBLE", True),  # roman single level, all caps
        ("13 References", True),  # template chapter in title case
        ("7. Milestones", True),
        ("11 Management and Reporting of Adverse Events/Adverse Reactions", True),
        ("1. Patients aged 18 years or older", False),  # a numbered list item
        ("2. Abstract submitted to the congress", False),  # template word, not the whole title
        ("0.03 MG/ML Injectable Suspension", False),  # a value, not a section number
        ("I agree to the terms of the study", False),
    ],
)
def test_heading_numbering_and_template_titles(line, expected):
    assert is_heading(line) is expected


def test_section_numbers_parse_roman_and_arabic_alike():
    assert section_number("II.4 Abstract") == (2, 4) == section_number("2.4 Abstract")
    assert section_number("IX. ANNEXES") == (9,) and section_number("0.03 MG/ML") == ()


def test_title_case_references_chapter_ends_the_methods_context():
    # Given a protocol without bookmarks whose reference chapter title is not in capitals
    pages = extract_pages(
        pdf(
            METHODS + "\n9.9 Limitations\nMisclassification of the outcome is possible in claims data.",
            "13 References\n1. Smith J, et al. Outcome validation in claims. Pharmacoepidemiol Drug Saf. 2019.",
        )
    )
    chunks = sections(pages)
    reference = chunk_with(chunks, "Smith J")
    # Then the reference list is its own chapter and is not read as part of the limitations section
    assert reference["section"] == "13 References" and reference["role"] == "references"
    assert reference not in reading_order(chunks)


def test_roman_chapters_set_the_parent_context():
    chunks = sections(
        extract_pages(
            pdf(
                "II.9 Research Methods\nA retrospective cohort design is used for the outcome analysis.",
                "III.13 References\nJones A, et al. Drug utilisation in Nordic registries. 2018.",
            )
        )
    )
    assert chunk_with(chunks, "retrospective")["role"] == "methods"
    assert chunk_with(chunks, "Jones")["role"] == "references"


def toc_pdf(printed_offset_ok: bool = True) -> bytes:
    """A title page, a contents page, then four body pages printed as pages 1-4 (physical 3-6)."""
    titles = ["1. Overview", "2. Work Packages", "3. Study Procedures", "4. Bibliographic Sources"]
    printed = [1, 2, 3, 4] if printed_offset_ok else [1, 4, 2, 9]
    contents = "\n".join(f"{t} {'.' * 20} {n}" for t, n in zip(titles, printed))
    body = [
        f"{t}\nThis chapter describes {t[3:].lower()} of the cohort study with outcome definitions."
        for t in titles
    ]
    return pdf("Study protocol title page with sponsor and version information.", contents, *body)


def test_verified_contents_page_becomes_the_outline():
    pages = extract_pages(toc_pdf())
    assert [p.outline_source for p in pages[2:]] == ["toc"] * 4
    assert pages[3].chapter == "2. Work Packages" and pages[5].chapter_top == "4. Bibliographic Sources"
    # A title-case chapter line named by the contents starts a section although is_heading rejects it
    assert not is_heading("2. Work Packages")
    assert chunk_with(sections(pages), "work packages of")["section"] == "2. Work Packages"


def test_contents_with_disagreeing_page_numbers_is_not_used():
    pages = extract_pages(toc_pdf(printed_offset_ok=False))
    assert all(p.outline_source is None for p in pages)


def test_pdf_bookmarks_take_precedence_over_the_contents_page():
    with pymupdf.open(stream=toc_pdf(), filetype="pdf") as doc:
        doc.set_toc([[1, "Body", 3]])
        data = doc.tobytes()
    pages = extract_pages(data)
    assert pages[4].chapter == "Body" and pages[4].outline_source == "bookmarks"
    assert extract_pages(data, use_outline=False)[4].outline_source == "toc"


def styled_pdf(lines: list[tuple[str, bool]]) -> bytes:
    """One page; (text, is_chapter_style) lines: chapter style is a larger bold font, body text is 9 pt."""
    with pymupdf.open() as doc:
        page = doc.new_page()
        y = 40
        for text, chapter in lines:
            page.insert_text(
                (40, y), text, fontsize=13 if chapter else 9, fontname="hebo" if chapter else "helv"
            )
            y += 20
        return doc.tobytes()


def test_layout_and_chapter_sequence_find_title_case_chapters_without_an_outline():
    body = "The cohort includes adults with a first dispensing and outcome follow-up of two years."
    pages = extract_pages(
        styled_pdf(
            [
                ("4. ABSTRACT", True),
                (body, False),
                ("5 Study Procedures", True),  # next chapter number, larger bold font
                (body, False),
                ("1. Patients aged 18 years or older", True),  # bold list item: breaks the sequence
                (body, False),
                ("6 Work Packages", False),  # next number but body font: no layout support
                (body, False),
            ]
        )
    )
    headings = {c["section"] for c in sections(pages)}
    assert "5 Study Procedures" in headings
    assert "1. Patients aged 18 years or older" not in headings
    assert "6 Work Packages" not in headings


QUESTIONNAIRE = (
    "Section 1: Milestones Yes No N/A Section Number\n"
    "1.1 Does the protocol specify timelines for the start of data collection?\n"
    "1.2 Does the protocol specify the end of data collection?\n"
    "2.1 Does the formulation of the research question clearly explain why the study is conducted?\n"
    "3.1 Is the study design described?\n"
    "4.1 Is the source population described?"
)


def test_checklist_range_starts_at_its_title_and_ends_at_the_next_appendix():
    # Given methods text and the checklist title on one page, the questionnaire, then another annex
    chunks = sections(
        extract_pages(
            pdf(
                METHODS
                + "\nAPPENDIX 5 - ENCEPP CHEKLIST\nENCePP Checklist for Study Protocols (Revision 4)\n"
                + QUESTIONNAIRE,
                "9.2 Does the protocol describe the data sources?\n10.1 Are missing data handled?\n"
                "APPENDIX 6 - CODE LIST\nICD-10 K85 acute pancreatitis defines the outcome.",
            )
        )
    )
    checklist = [c for c in chunks if c["role"] == "checklist"]
    # Then the questionnaire is one block across both pages, despite the misspelt appendix heading
    assert [c["page"] for c in checklist] == [1, 2]
    assert checklist[0]["text"].startswith("ENCePP Checklist for Study Protocols")
    assert not any(c["relevant"] for c in checklist)
    # And the text before it and the next annex keep their own sections
    assert chunk_with(chunks, "retrospective cohort")["role"] == "methods"
    assert chunk_with(chunks, "K85")["role"] == "appendix"


def test_checklist_title_in_a_list_of_annexes_is_not_the_checklist():
    chunks = sections(
        extract_pages(
            pdf(
                "17. ANNEXES\nAppendix I: Lists with concept definitions for exposure\n"
                "Appendix III: ENCePP checklist for study protocols",
                "APPENDIX I: Lists with concept definitions for exposure\nnaloxone 1114220 buprenorphine 45776270",
            )
        )
    )
    assert not any(c["role"] == "checklist" for c in chunks)
    listing = chunk_with(chunks, "Appendix III: ENCePP checklist for study protocols")
    assert "no questionnaire follows" in listing["structure_warnings"][0]
    assert chunk_with(chunks, "naloxone")["role"] == "appendix"
