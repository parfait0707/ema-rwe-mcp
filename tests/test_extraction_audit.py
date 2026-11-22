"""Deterministic post-extraction audits: redaction, catalogue sources, unquoted terms, section labels."""

import pymupdf
import pytest
from test_core import sample_analysis

from ema_rwe.domain import Evidence, Extraction, Fact, RWEError
from ema_rwe.pdf import Page, audit_extraction, extract_pages, finalize_extraction, prune_unverifiable


def test_audit_flags_redaction_near_outcome_text():
    pages = [
        Page(1, "Two types of outcomes will be defined: hospitalized AMI and CCI. Endpoint table redacted.")
    ]
    notes = audit_extraction(Extraction(outcomes=[]), pages)
    assert any("Redacted (CCI)" in n and "[1]" in n for n in notes)


def test_audit_reports_catalogue_sources_missing_from_extraction():
    analysis = sample_analysis()  # extracts "Example Primary Care Database"
    notes = audit_extraction(
        analysis, [Page(1, "x")], ["Example Primary Care Database", "National Claims Warehouse"]
    )
    assert len(notes) == 1 and "National Claims Warehouse" in notes[0] and "Example Primary" not in notes[0]


def test_audit_reports_value_terms_absent_from_quotes():
    analysis = sample_analysis()
    analysis.comparator = Fact(
        value="Romosozumab versus Alendronate 70 mg",
        evidence=[Evidence(page=1, quote="pairwise treatment comparison")],
    )
    notes = audit_extraction(analysis, [Page(1, "x")])
    assert any("comparator" in n and "Romosozumab" in n and "Alendronate" in n for n in notes)
    analysis.comparator.evidence[0].quote = "Romosozumab versus Alendronate 70 mg daily"
    assert not any("comparator" in n for n in audit_extraction(analysis, [Page(1, "x")]))


CODE_TABLE = "Outcome codes: I21 I21.0 I22 I63 I64 G45 E11.9 A10BA02 250.00 410.1"


def test_audit_reports_code_pages_no_fact_cites():
    """spec v1.32: a page listing codes that no quote cites is reported; once cited, it is not."""
    pages = [Page(1, "Study design text without codes."), Page(2, CODE_TABLE)]
    analysis = sample_analysis()
    assert any("Pages listing codes" in n and "[2]" in n for n in audit_extraction(analysis, pages))
    analysis.disease_definitions = [
        Fact(value="Outcome codes", evidence=[Evidence(page=2, quote="Outcome codes: I21 I21.0")])
    ]
    assert not any("Pages listing codes" in n for n in audit_extraction(analysis, pages))


def test_a_few_codes_do_not_make_a_code_page():
    pages = [Page(1, "Diabetes is defined by ICD-10 E11 and E11.9 diagnoses (see Annex A01).")]
    assert not any("Pages listing codes" in n for n in audit_extraction(Extraction(), pages))


def test_scan_with_one_typed_page_requires_ocr():
    """spec v1.32: one approval page of text over a scanned body is no usable text layer."""
    with pymupdf.open() as doc:
        for _ in range(4):
            doc.new_page()
        doc[3].insert_text((50, 50), "Document Approval Record\n" + "Approved by the study sponsor.\n" * 10)
        data = doc.tobytes()
    with pytest.raises(RWEError) as raised:
        extract_pages(data)
    assert raised.value.code == "PDF_OCR_REQUIRED"


def test_provider_section_labels_are_rederived_from_quote_location(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    analysis = sample_analysis()
    analysis.study_design.evidence[0].section = "9.9 Wrong heading carried over"
    pruned, dropped = prune_unverifiable(analysis, pages)
    assert dropped == 0 and pruned.study_design.evidence[0].section == "8.1 Study design"


def test_finalize_appends_dropped_count_and_audit_notes(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    analysis = sample_analysis()
    analysis.outcomes = [
        Fact(value="fabricated", evidence=[Evidence(page=1, quote="this is not in the pdf at all")])
    ]
    pruned, dropped = finalize_extraction(analysis, pages, ["Unknown Registry"])
    assert dropped == 1 and pruned.outcomes == []
    assert any("were dropped" in n for n in pruned.missing_information)
    assert any("Unknown Registry" in n for n in pruned.missing_information)
