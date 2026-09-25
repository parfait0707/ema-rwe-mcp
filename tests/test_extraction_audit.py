"""Deterministic post-extraction audits: redaction/count claims, catalogue sources, unquoted terms, section labels."""

from test_core import sample_analysis

from ema_rwe.domain import Evidence, Extraction, Fact
from ema_rwe.pdf import Page, audit_extraction, extract_pages, finalize_extraction, prune_unverifiable


def test_audit_flags_redaction_and_stated_outcome_count():
    pages = [
        Page(1, "Two types of outcomes will be defined: hospitalized AMI and CCI. Endpoint table redacted.")
    ]
    notes = audit_extraction(Extraction(outcomes=[]), pages)
    assert any("Redacted (CCI)" in n and "[1]" in n for n in notes)
    assert any("states two outcomes" in n and "0 outcome definition(s)" in n for n in notes)
    enough = Extraction(
        outcomes=[
            Fact(value=f"o{i}", evidence=[Evidence(page=1, quote="hospitalized AMI")]) for i in range(2)
        ]
    )
    assert not any("states two" in n for n in audit_extraction(enough, pages))


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
