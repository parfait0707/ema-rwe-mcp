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


def test_verified_contents_page_marks_where_sections_start():
    pages = extract_pages(toc_pdf())
    assert [p.outline_source for p in pages[2:]] == ["toc"] * 4
    # Section starts only: chapters and roles keep coming from the text, since contents pages can drift
    assert all(p.chapter is None for p in pages)
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


def test_bookmarks_under_a_list_of_tables_are_navigation_not_chapters():
    # Given a list-of-tables bookmark whose child points at a table on a later page of the methods chapter
    data = pdf(
        "LIST OF TABLES\nTable 3 Outcome codes ....",
        METHODS,
        "Table 3 Outcome codes\nThe index date is the first dispensing; follow-up ends at the outcome.",
        # As many PDFs do, the list entry itself points at the first table in the body, not at the list
        toc=[[1, "9 Research methods", 2], [1, "List of Tables", 3], [2, "Table 3 Outcome codes", 3]],
    )
    pages = extract_pages(data)
    # Then that page stays in the methods chapter and is read
    assert pages[2].chapter == "9 Research methods" and pages[2].chapter_top == "9 Research methods"
    chunks = sections(pages)
    assert chunk_with(chunks, "index date")["role"] != "contents"
    assert chunk_with(chunks, "index date") in reading_order(chunks)


def test_conduct_chapter_is_read_even_without_method_words():
    chunks = sections(
        extract_pages(
            pdf(
                METHODS,
                "10. PROTECTION OF HUMAN SUBJECTS\nApproval will be obtained before any data are recorded.",
            )
        )
    )
    section = chunk_with(chunks, "Approval will be obtained")
    assert section["role"] == "conduct" and section in reading_order(chunks)


def test_text_above_an_outline_title_keeps_the_previous_chapter():
    # Given the references chapter starting midway down a page, under the end of the limitations section
    pages = extract_pages(
        pdf(
            METHODS + "\n9.9 Limitations\nMisclassification of the outcome is possible.",
            "Exposure misclassification may also bias the comparison towards the null.\n13 References\n"
            "1. Smith J, et al. Outcome validation in claims. 2019.",
            toc=[[1, "9 Research methods", 1], [1, "13 References", 2]],
        )
    )
    chunks = sections(pages)
    # Then the leading text stays in the methods chapter and is read; the reference list is not
    tail = chunk_with(chunks, "towards the null")
    assert tail["chapter"] == "9 Research methods" and tail["role"] != "references"
    assert tail in reading_order(chunks)
    assert chunk_with(chunks, "Smith J")["role"] == "references"


def test_unnumbered_contents_lines_are_sub_items_of_the_numbered_entry():
    from ema_rwe.pdf import Page, text_outline

    contents = "\n".join(
        f"{t} {'.' * 12} {n}"
        for t, n in [
            ("1. Overview", 1),
            ("- Pre-pandemic period", 1),
            ("2. Methods", 2),
            ("Annex 1 Code list", 3),
        ]
    )
    pages = [Page(1, "Title page"), Page(2, contents)]
    pages += [
        Page(3, "1. Overview\n- Pre-pandemic period\nText."),
        Page(4, "2. Methods\nText."),
        Page(5, "Annex 1 Code list\nK85"),
    ]
    levels = {title: level for level, title, _ in text_outline(pages)}
    assert levels == {"1. Overview": 1, "- Pre-pandemic period": 2, "2. Methods": 1, "Annex 1 Code list": 1}


def test_appendix_filter_needs_the_pdfs_own_bookmarks():
    # A contents-page outline keeps every relevant section, including an appendix the body never cites
    data = toc_pdf()
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        doc.new_page().insert_text(
            (40, 40),
            "Annex 9 Selection criteria\nICD-10 K85 acute pancreatitis code list for the outcome.",
            fontsize=8,
        )
        data = doc.tobytes()
    chunks = sections(extract_pages(data))
    assert chunk_with(chunks, "K85") in reading_order(chunks)


def test_outline_ancestry_places_a_section_whose_own_title_has_no_role():
    # Given two protocols in one PDF: chapter III starts on the same page as its responsible-parties
    # section, and its methods subsection 'III.9.2 Setting' has no role of its own
    data = pdf(
        "III PATIENT REGISTRY STUDY\nThis registry follows adults with type 2 diabetes.\n"
        "III.3 Responsible Parties\nThe sponsor is responsible for the registry conduct.",
        "III.9.2 Setting\nPatients will be identified in the national diabetes register and followed for outcomes.",
        toc=[
            [1, "Programme of studies", 1],
            [2, "III Patient Registry Study", 1],
            [3, "III.3 Responsible Parties", 1],
            [3, "III.9 Research Methods", 2],
            [4, "III.9.2 Setting", 2],
        ],
    )
    chunks = sections(extract_pages(data))
    # Then the chapter heading belongs to its own entry, not to the last entry on its page
    assert chunk_with(chunks, "follows adults")["chapter"] == "III Patient Registry Study"
    # And the setting takes 'methods' from its outline ancestor and is read
    setting = chunk_with(chunks, "diabetes register")
    assert setting["role"] == "methods" and setting["role_basis"] == "bookmark"
    assert setting in reading_order(chunks)


def test_objectives_inside_a_background_chapter_keep_their_role():
    chunks = sections(
        extract_pages(
            pdf(
                "7. RATIONALE AND BACKGROUND\n7.1. Treatment of depression\nEarlier studies reported the risk.\n"
                "7.4 Research Question and Objectives\nThe objective is to estimate the risk of malformations.",
                "13. REFERENCES\n13.1 Missing data\nPrior authors used complete case analysis.",
            )
        )
    )
    assert chunk_with(chunks, "Earlier studies")["role"] == "background"
    objectives = chunk_with(chunks, "estimate the risk")
    assert objectives["role"] == "objectives" and objectives in reading_order(chunks)
    # A method-like heading in a reference list is still the reference list
    assert chunk_with(chunks, "Prior authors")["role"] == "references"


def test_new_top_level_chapter_ends_the_previous_parent_even_without_a_known_role():
    data = pdf(
        "BACKGROUND\nEarlier cohorts reported neurodevelopmental effects of exposure in utero.",
        "PROTOCOL 1 NEURODEVELOPMENTAL FOLLOW-UP\n1.1 Recruitment of children\nChildren exposed in utero will be recruited.",
        toc=[
            [1, "Background", 1],
            [1, "Protocol 1 Neurodevelopmental follow-up", 2],
            [2, "1.1 Recruitment of children", 2],
        ],
    )
    chunks = sections(extract_pages(data))
    recruitment = chunk_with(chunks, "will be recruited")
    assert recruitment["role"] != "background" and recruitment["context_role"] != "background"


def test_consecutive_unnumbered_contents_lines_are_siblings():
    from ema_rwe.pdf import Page, text_outline

    entries = [("Background", 1), ("Aims", 1), ("Objectives", 1), ("1.1 Recruitment", 2), ("Notes", 2)]
    contents = "\n".join(f"{t} {'.' * 12} {n}" for t, n in entries)
    pages = [Page(1, "Title page"), Page(2, contents)]
    pages += [Page(3, "Background\nAims\nObjectives\nText."), Page(4, "1.1 Recruitment\nNotes\nText.")]
    levels = {title: level for level, title, _ in text_outline(pages)}
    assert levels == {"Background": 1, "Aims": 1, "Objectives": 1, "1.1 Recruitment": 2, "Notes": 3}


def test_contents_outline_skips_table_captions_and_places_unprinted_annex_headings():
    from ema_rwe.pdf import Page, text_outline

    entries = [
        ("1. Overview", 1),
        ("2. Methods", 2),
        ("3. References", 3),
        ("Annex I: Code lists", 4),
        ("Table A1.2. Product codes", 5),
    ]
    contents = "\n".join(f"{t} {'.' * 12} {n}" for t, n in entries)
    pages = [
        Page(1, "Title page"),
        Page(2, contents),
        Page(3, "1. Overview\nText."),
        Page(4, "2. Methods\nText."),
    ]
    # The annex pages are rotated tables: no heading line is printed, only the caption of a table
    pages += [
        Page(5, "3. References\nSmith J, et al."),
        Page(6, "B01AA03 B01AF01"),
        Page(7, "Table A1.2. Product codes\n4446"),
    ]
    rows = {title: (level, page) for level, title, page in text_outline(pages)}
    assert rows["Annex I: Code lists"] == (1, 6)  # placed by the verified offset
    assert "Table A1.2. Product codes" not in rows  # navigation, not structure


def test_sections_are_read_when_the_role_words_do_not_know_the_language():
    # A Spanish protocol: no heading has a known role and the English signal words do not occur
    chunks = sections(
        extract_pages(
            pdf(
                "8. DISEÑO DEL ESTUDIO\nEstudio de casos y controles anidado.\n"
                "9. POBLACIÓN EN ESTUDIO\n9.1 Criterios de exclusión\nPacientes con cáncer previo al diagnóstico.",
                "11. PLAN DE TRABAJO\nLos datos se extraerán en 2024 por el equipo investigador del centro.",
            )
        )
    )
    exclusion = chunk_with(chunks, "cáncer previo")
    assert exclusion["role"] == "unknown" and exclusion in reading_order(chunks)


def test_code_rows_are_not_section_numbers_and_code_tables_after_references_are_read():
    chunks = sections(
        extract_pages(
            pdf(
                METHODS + "\n10 References\n1. Smith J, et al. Pharmacoepidemiol Drug Saf. 2019;28:1-9.\n"
                "2. Jones A, et al. BMJ. 2018;360:k1. doi:10.1136/bmj.k1",
                "11 Tables\nTable 3 READ codes for acute liver injury\nJ600.00 Acute and subacute liver necrosis\n"
                "79.15 Closed reduction of fracture with internal fixation, femur\nJ601.00 Subacute liver necrosis\n"
                + "\n".join(
                    f"J6{n:02}.00 Liver disorder code {n} for the outcome definition" for n in range(2, 9)
                ),
            )
        )
    )
    assert not any(c["section"] and c["section"].startswith("79.15") for c in chunks)
    table = chunk_with(chunks, "J600.00")
    assert table["role"] != "references" and table in reading_order(chunks)
    assert chunk_with(chunks, "Smith J")["role"] == "references"


def test_unnamed_subsections_inside_the_body_are_read():
    chunks = sections(
        extract_pages(
            pdf(
                METHODS,
                "5. STUDY PROCEDURES\n5.5.1 Primary endpoint\nTime to first exacerbation requiring hospital care.",
            )
        )
    )
    endpoint = chunk_with(chunks, "first exacerbation")
    assert endpoint["role"] == "unknown" and endpoint in reading_order(chunks)


def test_method_subsection_with_rationale_in_its_title_is_not_background():
    assert heading_role("7.3.1 Context and rationale for definition of time 0") == "unknown"
    assert heading_role("5. Rationale and background") == heading_role("RATIONALE") == "background"


def test_unnumbered_heading_with_a_known_role_ends_the_background_context():
    chunks = sections(
        extract_pages(
            pdf(
                "BACKGROUND\nIdiopathic pulmonary fibrosis is often diagnosed late in primary care.\n"
                "STUDY DESIGN\nA historical cohort study in UK primary care records.\n"
                "Clinical characterisation at time of diagnosis\nPrescriptions and spirometry in the two years before.",
            )
        )
    )
    section = chunk_with(chunks, "spirometry")
    assert section["role"] != "background" and section in reading_order(chunks)


def test_amendment_annex_and_outcome_definition_appendix_are_read():
    assert heading_role("Annex 7 AMENDMENTS TO THE PROTOCOL") == "amendments"
    data = pdf(
        METHODS,
        "Staging\nAnn Arbor stage III or IV at diagnosis defines advanced lymphoma for the outcome.",
        toc=[
            [1, "9 Research methods", 1],
            [1, "17. ANNEXES", 2],
            [2, "Appendix I: Definitions of study outcomes", 2],
            [3, "Diffuse large B-cell lymphoma", 2],
            [4, "Staging", 2],
        ],
    )
    chunks = sections(extract_pages(data))
    # The deepest bookmark ('Staging') names no code list, but its appendix defines the outcomes
    assert chunk_with(chunks, "Ann Arbor") in reading_order(chunks)


def test_annexes_listed_under_a_list_of_annexes_bookmark_are_structure():
    data = pdf(
        METHODS,
        "Version history of the protocol: the 6-month washout was removed from the sensitivity analysis.",
        toc=[
            [1, "9 Research methods", 1],
            [1, "LIST OF ANNEXES", 2],
            [2, "Annex 7 AMENDMENTS TO THE PROTOCOL", 2],
        ],
    )
    pages = extract_pages(data)
    assert pages[1].chapter == "Annex 7 AMENDMENTS TO THE PROTOCOL"
    assert chunk_with(sections(pages), "washout") in reading_order(sections(pages))


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("DATA SOURCE, STUDY DESIGN AND METHODOLOGY", True),  # unnumbered all-caps method heading
        ("CONFIDENTIAL", False),
        ("3.3 million, by applying the prevalence of the deficiency", False),  # a value in running text
        ("Appendix  I. The  following  protocols  will  continue  to be  developed  taking  into", False),
        ("Annex 5 CASE DEFINITIONS FOR ADVERSE EVENTS OF SPECIAL INTEREST AND SURVEILLANCE INDICATORS", True),
    ],
)
def test_heading_candidates_from_the_review_of_real_protocols(line, expected):
    assert is_heading(line) is expected


def test_goal_is_an_objective_and_a_cited_appendix_is_read_from_any_chapter():
    assert heading_role("4.3 Goal") == "objectives"
    data = pdf(
        "1. BACKGROUND\nThe synopsis of the database study is attached as Appendix I for reference purposes.",
        METHODS,
        "Appendix I\nThe sample size of 10,000 person-years gives 80% power for the outcome comparison.",
        toc=[[1, "1 Background", 1], [1, "9 Research methods", 2], [1, "Appendix I Study synopsis", 3]],
    )
    chunks = sections(extract_pages(data))
    assert chunk_with(chunks, "sample size") in reading_order(chunks)


def test_english_detection():
    from ema_rwe.pdf import Page, english

    assert english(
        [Page(1, "The study will use the data of the registry and the outcome is defined in the protocol.")]
    )
    assert not english(
        [Page(1, "Se realizará un estudio de casos y controles con datos procedentes de la base.")]
    )
