"""Parsed-PDF cache, space compaction for readers, data source names and the per-tool timing log (spec v1.12)."""

import logging
import threading

import pymupdf
import pytest
from test_core import sample_analysis

from ema_rwe import pdf
from ema_rwe.domain import DataSource, Evidence, Extraction, RWEError
from ema_rwe.pdf import (
    compact_spaces,
    extract_pages,
    name_span,
    parse_stats,
    sections,
    validate_evidence,
    with_source_name_quotes,
)

FILLER = (
    "\n9.9 Analysis\nDescriptive statistics will summarise the baseline characteristics of the study cohort, "
    "and incidence rates will be estimated with their 95 percent confidence intervals."
)


def one_page(text: str) -> bytes:
    """A one-page PDF with enough text to count as having a text layer."""
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), text + FILLER, fontsize=8)
        return doc.tobytes()


def test_same_pdf_and_reading_is_parsed_once(pdf_bytes):
    before = parse_stats()
    first = extract_pages(pdf_bytes)
    assert extract_pages(pdf_bytes) is first
    after = parse_stats()
    assert after["parsed"] - before["parsed"] <= 1 and after["reused"] - before["reused"] >= 1
    # Another reading (heading translations) or outline setting is another parse of the same bytes
    assert extract_pages(pdf_bytes, translations={"8.1 Study design": "8.1 Design"}) is not first
    assert extract_pages(pdf_bytes, use_outline=False) is not first


def test_callers_get_their_own_section_chunks(pdf_bytes):
    pages = extract_pages(pdf_bytes)
    chunks = sections(pages)
    chunks[0]["text"] = None
    chunks[0]["added"] = True
    again = sections(pages)
    assert again[0]["text"] and "added" not in again[0]


def test_failed_parse_is_not_cached():
    for _ in range(2):
        with pytest.raises(RWEError) as error:
            extract_pages(b"%PDF-1.4 not a document")
        assert error.value.code == "PDF_PARSE_FAILED"


def test_concurrent_requests_for_one_pdf_parse_it_once(monkeypatch):
    data = one_page("9.1 Study design\nA cohort study of adults in one national registry over ten years.")
    calls, release = [], threading.Event()
    original = pdf._parse_pages

    def slow(*args):
        calls.append(1)
        release.wait(5)
        return original(*args)

    monkeypatch.setattr(pdf, "_parse_pages", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(extract_pages(data))) for _ in range(6)]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(10)
    assert len(calls) == 1 and len(results) == 6 and all(r is results[0] for r in results)


def test_cache_keeps_only_the_most_recent_pdfs(monkeypatch):
    monkeypatch.setattr(pdf, "PARSE_CACHE_SIZE", 2)
    documents = [
        one_page(f"9.{i} Study design\nA cohort study number {i} of adults in a registry.") for i in range(3)
    ]
    first = extract_pages(documents[0])
    for data in documents[1:]:
        extract_pages(data)
    assert extract_pages(documents[0]) is not first  # evicted, parsed again


def test_reader_text_loses_layout_spaces_and_its_quotes_still_verify():
    data = one_page(
        "9.4 Data sources\nSource                         Since\n"
        "Example Cancer Registry                    1943\nThe study will use the Example Cancer Registry."
    )
    pages = extract_pages(data)
    raw = sections(pages)
    compact = compact_spaces(sections(pages))
    row = next(c for c in compact if "1943" in c["text"])
    assert "Example Cancer Registry  1943" in row["text"]
    assert sum(len(c["text"]) for c in compact) < sum(len(c["text"]) for c in raw)
    quote = "Example Cancer Registry  1943"  # copied from the shortened text
    source = DataSource(
        value="Example Cancer Registry", usage="planned", evidence=[Evidence(page=1, quote=quote)]
    )
    validate_evidence(Extraction(data_sources=[source]), pages)


@pytest.mark.parametrize(
    "name,text,found",
    [
        ("CPRD Aurum", "data from CPRD-Aurum will be used", True),  # punctuation and case aside
        ("cprd-aurum", "CPRD Aurum", True),
        # A table breaks the name over two lines with another column's cell in between
        (
            "Income and education registries from Statistics Denmark",
            "registries from     -\n   Statistics Denmark",
            False,
        ),
        (
            "Income and education registries from Statistics Denmark",
            "Income and education registries from     -\n        Statistics Denmark\n Sweden",
            True,
        ),
        ("Swedish Prescribed Drug Register", "The Swedish Prescribed Drug Register   July 2005", True),
        ("HES", "the hesitant patients", False),  # whole words only
        ("SDRN-EPI (Scotland)", "SDRN-EPI will provide data", False),  # a word the text does not write
        (
            "Danish Cancer Registry",
            "Danish registries hold no data on the national cancer burden; the registry",
            False,
        ),
        ("()", "anything", None),
    ],
)
def test_data_source_name_is_matched_by_its_words(name, text, found):
    assert bool(name_span(name, text)) is bool(found)


def test_source_named_on_its_page_gets_the_naming_quote():
    data = one_page(
        "9.4 Data sources\nNational Example Prescription Register (NEPR)\n"
        "Dispensing records of all pharmacies are available from 2005 onwards."
    )
    pages = extract_pages(data)
    usage = Evidence(page=1, quote="Dispensing records of all pharmacies are available from 2005 onwards.")
    source = DataSource(value="National Example Prescription Register", usage="planned", evidence=[usage])
    with pytest.raises(RWEError) as error:  # the caller's quote alone does not name the source
        validate_evidence(Extraction(data_sources=[source]), pages)
    assert (
        error.value.code == "EVIDENCE_INVALID"
        and "National Example Prescription Register" in error.value.message
    )
    (named,) = with_source_name_quotes([source], pages)
    assert named.evidence[0] == usage and "National Example Prescription Register" in named.evidence[1].quote
    validate_evidence(Extraction(data_sources=[named]), pages)
    # A name the page does not write is left for validation to refuse
    invented = source.model_copy(update={"value": "National Example Dispensing Database"})
    assert with_source_name_quotes([invented], pages) == [invented]
    with pytest.raises(RWEError):
        validate_evidence(Extraction(data_sources=with_source_name_quotes([invented], pages)), pages)


async def test_caller_batch_with_a_source_quote_that_omits_the_name_is_saved(service):
    first = await service.analyze_protocol("123")
    analysis = sample_analysis()
    # The quote supports the fact but leaves the name to the sentence before it on the same page
    analysis.data_sources[0].evidence[0] = Evidence(
        page=1, quote="Diabetes is defined by two ICD-10 E11 diagnoses in 365 days."
    )
    done = await service.cache_protocol_analysis("123", first["source"]["fingerprint"], analysis, True)
    assert done["status"] == "analyzed"
    saved = service.repo.analysis("123")["analysis"]["data_sources"][0]
    assert len(saved["evidence"]) == 2 and "Example Primary Care Database" in saved["evidence"][1]["quote"]


async def test_tool_calls_are_timed_on_the_log(service, caplog):
    from ema_rwe.mcp.server import create_server

    server = create_server(service)
    with caplog.at_level(logging.INFO, logger="ema_rwe.mcp.server"):
        await server._tool_manager.call_tool("catalogue_status", {})
        await server._tool_manager.call_tool("analyze_protocol", {"study_id": "123"})
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tool=")]
    assert lines[0].startswith("tool=catalogue_status ms=") and "chars=" in lines[0]
    assert "tool=analyze_protocol" in lines[1] and "status=needs_client_extraction" in lines[1]
    assert "pdf_parsed=" in lines[1] and "pdf_parse_ms=" in lines[1]


def two_pages(first: str, second: str) -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), first + FILLER, fontsize=8)
        doc.new_page().insert_text((50, 50), second + FILLER, fontsize=8)
        return doc.tobytes()


def test_source_named_on_another_page_gets_the_naming_quote_from_there():
    data = two_pages(
        "9.4 Data sources\nThe National Example Hospital Register (NEHR) holds all inpatient stays since 1995.",
        "9.5 Study size\nAbout 20,000 eligible patients are expected in NEHR during the study period.",
    )
    pages = extract_pages(data)
    cited = Evidence(
        page=2, quote="About 20,000 eligible patients are expected in NEHR during the study period."
    )
    source = DataSource(value="National Example Hospital Register (NEHR)", usage="planned", evidence=[cited])
    (named,) = with_source_name_quotes([source], pages)
    assert [e.page for e in named.evidence] == [2, 1]
    assert "National Example Hospital Register (NEHR)" in named.evidence[1].quote
    validate_evidence(Extraction(data_sources=[named]), pages)


def test_a_name_written_only_in_the_reference_list_is_not_evidence():
    data = two_pages(
        "9.4 Data sources\nThe study will use routinely collected hospital data from one national source.",
        "References\n1. Smith J, et al. The Remote Example Claims Database: a profile. J Epidemiol 2019;1:1-9. doi:10.1/x",
    )
    pages = extract_pages(data)
    assert {c["role"] for c in sections(pages) if c["page"] == 2} == {"references"}
    cited = Evidence(
        page=1, quote="The study will use routinely collected hospital data from one national source."
    )
    source = DataSource(value="Remote Example Claims Database", usage="planned", evidence=[cited])
    assert with_source_name_quotes([source], pages) == [source]
    with pytest.raises(RWEError):
        validate_evidence(Extraction(data_sources=[source]), pages)


def test_quote_on_another_page_is_moved_to_the_nearest_page_that_holds_it():
    from ema_rwe.domain import Fact
    from ema_rwe.pdf import with_corrected_pages

    data = two_pages(
        "9.1 Study design\nThis is a retrospective cohort study of adult patients in one country.",
        "9.2 Setting\nPatients are followed from the index date until death or the end of the study.",
    )
    pages = extract_pages(data)
    here = Evidence(page=1, quote="This is a retrospective cohort study of adult patients in one country.")
    there = Evidence(
        page=1,
        section="9.1 Study design",
        quote="Patients are followed from the index date until death or the end of the study.",
    )
    nowhere = Evidence(page=1, quote="A sentence that the protocol does not contain at all.")
    both = Evidence(
        page=1, quote="Descriptive statistics will summarise the baseline characteristics"
    )  # filler, pages 1 and 2
    extraction = Extraction(
        study_design=Fact(value="retrospective cohort", evidence=[here, there]),
        key_notes=[Fact(value="unknown", evidence=[nowhere]), Fact(value="analysis", evidence=[both])],
    )
    fixed = with_corrected_pages(extraction, pages)
    assert fixed.study_design.evidence[0] == here  # on the page given: untouched
    moved = fixed.study_design.evidence[1]
    assert (moved.page, moved.section, moved.server) == (2, None, "page")
    assert fixed.key_notes[0].evidence[0] == nowhere  # found nowhere: left for validation to refuse
    assert fixed.key_notes[1].evidence[0] == both  # on the page given, although also elsewhere
    validate_evidence(Extraction(study_design=fixed.study_design), pages)
    with pytest.raises(RWEError):
        validate_evidence(Extraction(key_notes=[fixed.key_notes[0]]), pages)


def test_a_quote_equally_near_on_both_sides_is_not_moved():
    from ema_rwe.pdf import with_corrected_pages

    with pymupdf.open() as doc:
        for text in (
            "9.1 Aims\nThe aims are described here in full detail.",
            "9.2 Design\nNothing of note on this page.",
            "9.3 Aims again\nThe aims are described here in full detail.",
        ):
            doc.new_page().insert_text(
                (50, 50), text + FILLER.replace("baseline", f"baseline {len(text)}"), fontsize=8
            )
        data = doc.tobytes()
    pages = extract_pages(data)
    ambiguous = Evidence(page=2, quote="The aims are described here in full detail.")
    extraction = Extraction(key_notes=[{"value": "aims", "evidence": [ambiguous]}])
    assert with_corrected_pages(extraction, pages).key_notes[0].evidence[0] == ambiguous


def test_server_added_naming_quote_is_marked_and_listed_apart_in_the_table():
    from ema_rwe.comparison import cited_pages

    data = two_pages(
        "9.4 Data sources\nThe National Example Hospital Register (NEHR) holds all inpatient stays since 1995.",
        "9.5 Study size\nAbout 20,000 eligible patients are expected in NEHR during the study period.",
    )
    pages = extract_pages(data)
    cited = Evidence(
        page=2, quote="About 20,000 eligible patients are expected in NEHR during the study period."
    )
    source = DataSource(value="National Example Hospital Register (NEHR)", usage="planned", evidence=[cited])
    (named,) = with_source_name_quotes([source], pages)
    assert [e.server for e in named.evidence] == [None, "source_name"]
    evidence = [e.model_dump() for e in named.evidence]
    assert cited_pages(evidence) == "p. 2; 名前の記載 p. 1"
    assert cited_pages(evidence[:1]) == "p. 2"
    assert cited_pages(evidence[1:]) == "p. 1"  # only the naming passage: it is all the evidence there is


async def test_caller_batch_with_a_quote_on_the_wrong_page_is_saved_with_the_page_corrected(service, website):
    from test_source_types import QUOTE, typed_pdf

    from ema_rwe.domain import Fact

    typed_pdf(website)  # adds a second page that holds QUOTE
    first = await service.analyze_protocol("123")
    assert first["total_pdf_pages"] == 2
    analysis = sample_analysis()
    analysis.key_notes = [Fact(value="cohort source", evidence=[Evidence(page=1, quote=QUOTE)])]
    done = await service.cache_protocol_analysis("123", first["source"]["fingerprint"], analysis, True)
    assert done["status"] == "analyzed"
    saved = service.repo.analysis("123")["analysis"]
    assert saved["key_notes"][0]["evidence"][0] == {
        "page": 2,
        "section": None,
        "quote": QUOTE,
        "server": "page",
    }
    assert saved["study_design"]["evidence"][0]["server"] is None
    # The limits a caller keeps running into are stated where it reads them
    assert "at least 8 characters" in first["instruction"] and "at most 8 quotes" in first["instruction"]
