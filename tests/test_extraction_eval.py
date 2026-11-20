"""The extraction reference checks (scripts/extraction_eval.py) on a small synthetic protocol; the real
reference protocols are checked by `scripts/check.py` when they are in the archive."""

import importlib.util
import json
from pathlib import Path

import pymupdf
import pytest

from ema_rwe.pdf import extract_pages, search_sections, sections

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("extraction_eval", ROOT / "scripts" / "extraction_eval.py")
evaluation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluation)

QUESTION = "What are the inclusion criteria and the exclusion criteria of the study population?"
FILL = "\nThe paragraph continues with further explanation of how the registers record these conditions over time."


def protocol_pdf() -> bytes:
    """Three pages: the eligibility section runs from page 1 to page 2, whose text never says 'criteria'."""
    pages = [
        "9.2 Selection criteria\nInclusion criteria of the study population are listed first.\n"
        "Patients aged 18 years or older at the index date.\nAt least 12 months of data before the index date."
        + FILL,
        "9.2 Selection criteria\nA history of kidney cancer on or before the index date.\n"
        "Maintenance dialysis on or before the index date." + FILL,
        "References\n1. Smith J, et al. Patients aged 18 years or older in registers. J Epidemiol 2019;1:1-9. doi:10.1/x",
    ]
    with pymupdf.open() as doc:
        for number, text in enumerate(pages, 1):
            page = doc.new_page()
            page.insert_text((50, 50), text, fontsize=9)
            doc.set_toc(doc.get_toc() + [[1, text.split("\n")[0], number]]) if number != 2 else None
        return doc.tobytes()


def reference(**changes) -> dict:
    items = [
        {
            "id": "I1",
            "kind": "inclusion",
            "label": "age 18 or older",
            "page": 1,
            "pattern": "18 years or older",
        },
        {
            "id": "I2",
            "kind": "inclusion",
            "label": "12 months of data",
            "page": 1,
            "pattern": "12 months of data",
        },
        {"id": "E1", "kind": "exclusion", "label": "kidney cancer", "page": 2, "pattern": "kidney cancer"},
        {
            "id": "E2",
            "kind": "exclusion",
            "label": "maintenance dialysis",
            "page": 2,
            "pattern": "Maintenance dialysis",
        },
    ]
    return {"study_id": "123", "sha256": "0" * 64, "pages": 3, "items": items, **changes}


def test_every_reference_item_of_a_sound_protocol_passes():
    result = evaluation.check_protocol(reference(), protocol_pdf(), QUESTION)
    assert result["pages"] == 3
    assert [(i["id"], i["problems"]) for i in result["items"]] == [
        ("I1", []),
        ("I2", []),
        ("E1", []),
        ("E2", []),
    ]


def test_an_item_the_page_does_not_hold_or_that_sits_in_the_references_fails():
    gone = reference()
    gone["items"][0] = {**gone["items"][0], "pattern": "aged 21 years"}
    gone["items"].append(
        {"id": "R", "kind": "inclusion", "label": "cited", "page": 3, "pattern": "in registers"}
    )
    problems = {
        i["id"]: i["problems"] for i in evaluation.check_protocol(gone, protocol_pdf(), QUESTION)["items"]
    }
    assert problems["I1"] == ["not found on page 1"]
    assert any("not served to the reader" in p or "refused" in p for p in problems["R"])
    assert problems["E1"] == []


def test_the_pages_a_section_continues_on_come_back_with_its_hit():
    chunks = sections(extract_pages(protocol_pdf()))
    found = search_sections(chunks, "inclusion", 5)["results"]
    second = next(c for c in found if c["page"] == 2)
    first = next(c for c in found if c["page"] == 1 and c["matched_terms"])
    # Page 2 does not say 'inclusion': it is returned because the section goes on there
    assert second["continues"] == first["section_id"] and second["matched_terms"] == []
    assert found.index(second) == found.index(first) + 1
    assert all(c["page"] != 3 or "continues" not in c for c in found)  # the reference list is another section
    # A hit that is itself a continuation page is not returned twice
    ids = [c["section_id"] for c in search_sections(chunks, "kidney cancer dialysis criteria", 5)["results"]]
    assert len(ids) == len(set(ids))


def test_missing_reference_protocols_are_reported_and_fail_only_when_required(settings, capsys):
    gold = {"question": QUESTION, "protocols": [reference()]}
    assert evaluation.check(settings, gold, require=False) == 0
    assert "0/1 protocols checked" in capsys.readouterr().out
    assert evaluation.check(settings, gold, require=True) == 1
    folder = evaluation.archive_dir(settings)
    folder.mkdir(parents=True)
    (folder / (evaluation.protocol_id(reference()) + ".pdf")).write_bytes(protocol_pdf())
    assert evaluation.check(settings, gold, require=True) == 0
    assert "1/1 protocols checked, 0 item(s) failing" in capsys.readouterr().out
    gold["protocols"][0]["pages"] = 9
    assert evaluation.check(settings, gold, require=True) == 1  # another document than the reference


def fact(value: str, quote: str) -> dict:
    return {"value": value, "evidence": [{"page": 1, "section": None, "quote": quote}]}


def test_grading_counts_found_and_supported_items():
    answer = {
        "answers": [
            fact("adults", "Patients aged 18 years or older at the index date."),
            fact(
                "12 months of data before index; no kidney cancer",
                "At least 12 months of data before the index date.",
            ),
        ]
    }
    analysis = {
        "cohort": {"inclusion_criteria": [fact("age", "aged 18 years or older")], "exclusion_criteria": []}
    }
    graded = evaluation.grade_protocol(reference(), answer, analysis)
    assert graded["answer_found"] == 3 and graded["answer_supported"] == 2  # kidney cancer: named, not quoted
    assert graded["cohort_found"] == 1 and graded["missing"] == ["E2"]
    nothing = evaluation.grade_protocol(reference(), None, None)
    assert (nothing["answered"], nothing["analysed"], nothing["answer_found"]) == (False, False, 0)


def test_the_reference_file_is_well_formed_and_holds_no_protocol_text():
    gold = evaluation.load()
    assert (
        gold["question"] == QUESTION
        and 0 < gold["minimum"]["cohort_found"] <= gold["minimum"]["answer_found"] <= 1
    )
    assert len(gold["protocols"]) == 4 and sum(len(p["items"]) for p in gold["protocols"]) == 51
    for protocol in gold["protocols"]:
        assert len(protocol["sha256"]) == 64 and protocol["document_url"].startswith(
            "https://catalogues.ema.europa.eu/"
        )
        ids = [item["id"] for item in protocol["items"]]
        assert len(ids) == len(set(ids))
        for item in protocol["items"]:
            assert set(item) - {"accept"} == {"id", "kind", "label", "page", "pattern"} and item["kind"] in (
                "inclusion",
                "exclusion",
            )
            assert 1 <= item["page"] <= protocol["pages"]
            # A locating pattern is a few key words, never a sentence of the protocol
            assert len(item["pattern"]) <= 45 and len(item["pattern"].split()) <= 7
    assert "quote" not in json.dumps(gold)


@pytest.mark.parametrize("command", ["check", "grade", "catalogue"])
def test_the_commands_run_on_an_empty_database(settings, command, monkeypatch, capsys):
    import asyncio

    gold = evaluation.load()
    if command == "check":
        assert evaluation.check(settings, gold, require=False) == 0
    elif command == "grade":
        assert evaluation.grade(settings, gold) == 1 and "nothing saved" in capsys.readouterr().out
    else:
        assert asyncio.run(evaluation.catalogue(settings, gold, online=False)) == 1
