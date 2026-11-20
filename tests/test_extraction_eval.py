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
    assert graded["analysis_found"] == 1 and graded["missing"] == ["E2"]
    assert graded["missing_in_analysis"] == ["I2", "E1", "E2"]
    # Another topic reads other blocks of the analysis
    outcomes = {
        "outcomes": [fact("dialysis", "Maintenance dialysis on or before")],
        "disease_definitions": [],
    }
    other = evaluation.grade_protocol(reference(), None, outcomes, ("outcomes", "disease_definitions"))
    assert other["analysis_found"] == 1 and other["missing_in_analysis"] == ["I1", "I2", "E1"]
    nothing = evaluation.grade_protocol(reference(), None, None)
    assert (nothing["answered"], nothing["analysed"], nothing["answer_found"]) == (False, False, 0)


def test_the_reference_files_are_well_formed_and_hold_no_protocol_text():
    golds = {gold["name"]: gold for gold in evaluation.load_all()}
    assert set(golds) == {"eligibility", "outcomes"} and golds["eligibility"]["question"] == QUESTION
    for gold in golds.values():
        assert 0 < gold["minimum"]["analysis_found"] <= gold["minimum"]["answer_found"] <= 1
        assert 0 < gold["minimum"]["answer_correct"] <= gold["minimum"]["answer_found"]
        assert gold["maximum"] == {"misplaced": 0}
        assert set(gold.get("kind_fields", {})) <= set(gold["kinds"])
        assert gold["analysis_fields"] and len(gold["protocols"]) == 4
        for protocol in gold["protocols"]:
            assert len(protocol["sha256"]) == 64 and protocol["document_url"].startswith(
                "https://catalogues.ema.europa.eu/"
            )
            ids = [item["id"] for item in protocol["items"]]
            assert ids and len(ids) == len(set(ids))
            for item in protocol["items"]:
                assert set(item) - {"accept", "beyond_first_search", "values"} == {
                    "id",
                    "kind",
                    "label",
                    "page",
                    "pattern",
                }
                assert item["kind"] in gold["kinds"] and 1 <= item["page"] <= protocol["pages"]
                # A locating pattern is a few key words, never a sentence of the protocol
                assert len(item["pattern"]) <= 45 and len(item["pattern"].split()) <= 7
        assert '"quote"' not in json.dumps(gold)
    # The topics read the same documents: each is fetched and looked up once
    assert len(evaluation.documents(list(golds.values()))["protocols"]) == 4


def test_a_question_is_searched_by_its_content_words_and_plain_plurals():
    """spec v1.21: 'from', 'which', 'each' rank nothing; 'outcome' finds the section 'Outcomes'; a
    lower-ranked page of a hit's section moves up beside it instead of staying at its own rank."""
    from ema_rwe.pdf import mentions

    assert mentions("9.3 Outcomes definition", "outcome") and mentions("the outcome", "outcomes")
    assert not mentions("class of medicines", "clas") and not mentions("I21 codes", "I2")
    chunks = sections(extract_pages(protocol_pdf()))
    found = search_sections(chunks, "From which criteria does each patient come?", 5)
    assert all(set(c["matched_terms"]) <= {"criteria", "patient", "come"} for c in found["results"])
    # Page 2 says 'kidney' only: a hit of its own, ranked below page 1, returned right after it
    ranked = search_sections(chunks, "inclusion criteria kidney", 5)["results"]
    pages = [c["page"] for c in ranked]
    assert pages.index(2) == pages.index(1) + 1
    moved = ranked[pages.index(2)]
    assert moved["continues"] == ranked[pages.index(1)]["section_id"] and "kidney" in moved["matched_terms"]


@pytest.mark.parametrize("command", ["check", "grade", "catalogue"])
def test_the_commands_run_on_an_empty_database(settings, command, monkeypatch, capsys):
    import asyncio

    gold = evaluation.load()
    assert gold["name"] == "eligibility"
    if command == "check":
        assert evaluation.check(settings, gold, require=False) == 0
    elif command == "grade":
        assert (
            asyncio.run(evaluation.grade(settings, gold)) == 1 and "nothing saved" in capsys.readouterr().out
        )
    else:
        assert asyncio.run(evaluation.catalogue(settings, gold, online=False)) == 1


def test_a_section_label_retyped_with_single_spaces_is_accepted():
    """PDF headings keep layout runs of spaces ('9.2   Selection criteria'); every reader of the reference
    run retyped them with one space and lost a round trip to EVIDENCE_INVALID (validation 2026-11-20)."""
    from ema_rwe.domain import Evidence, Extraction, Fact, RWEError
    from ema_rwe.pdf import validate_evidence

    pages = extract_pages(protocol_pdf())
    chunks = sections(pages)
    spaced = [dict(c, section="9.2   Selection criteria") if c["page"] == 1 else c for c in chunks]
    quote = "Patients aged 18 years or older at the index date."

    def note(section):
        return Extraction(
            key_notes=[Fact(value="age", evidence=[Evidence(page=1, quote=quote, section=section)])]
        )

    validate_evidence(note("9.2 Selection criteria"), pages, spaced)
    with pytest.raises(RWEError, match="Section does not match"):
        validate_evidence(note("9.3 Variables"), pages, spaced)


def test_the_neighbours_of_a_short_section_named_for_the_question_follow_it():
    """spec v1.23: '9.3.2 Outcomes definition' lists the outcomes in a few lines; '9.3.1 Exposure
    definition' beside it defines them. The neighbour that matched the question is ranked right after."""
    from ema_rwe.pdf import raise_neighbours

    def hit(section_id, section, score, text="x" * 300, role="definitions"):
        return {"section_id": section_id, "section": section, "score": score, "text": text, "role": role}

    hits = [
        hit("a", "9.4.2 A database", 9),
        hit("b", "9.3.2.    Outcomes definition", 5),
        hit("c", "9.3.1.    Exposure definition", 3),
        hit("d", "9.3.3.1.  Variables defining eligibility", 4),
        hit("e", "9.7.1 Analysis", 3),
        hit("f", "4. Abstract", 4),
    ]
    raise_neighbours(hits, ["outcome", "defined"])
    scores = {h["section_id"]: h["score"] for h in hits}
    assert scores == {"a": 9, "b": 5, "c": 4.5, "d": 4.5, "e": 3, "f": 4}
    assert hits[2]["beside"] == "b" and "beside" not in hits[4]
    # A long section named for the question holds its own definitions: its neighbours stay where they are
    long = [hit("b", "9.3.2 Outcome variables", 9, "x" * 1500), hit("c", "9.3.3 Other variables", 3)]
    raise_neighbours(long, ["outcome"])
    assert long[1]["score"] == 3
    # A chapter-level heading has no neighbours of this kind
    top = [hit("b", "9.3 Outcomes", 9), hit("c", "9.4 Data sources", 3)]
    raise_neighbours(top, ["outcome"])
    assert top[1]["score"] == 3


def test_grading_checks_required_values_the_kind_and_lists_what_matches_nothing():
    """spec v1.24: 'aged 16 or older' touches the age criterion but is not correct; an exclusion criterion
    listed as an inclusion criterion is misplaced; a fact that matches no item is listed, not failed."""
    protocol = reference()
    protocol["items"][0]["accept"] = "years or older|adults"
    protocol["items"][0]["values"] = ["18"]
    protocol["items"][1]["values"] = ["12", "index"]
    kinds = {"inclusion": "cohort.inclusion_criteria", "exclusion": "cohort.exclusion_criteria"}
    answer = {
        "answers": [
            fact("Patients aged 16 years or older", "Patients aged 18 years or older at the index date."),
            fact("12 months of data", "At least 12 months of data"),
            fact("the 12 months of data end at the index date", "At least 12 months of data"),
            fact("The study runs in three countries", "The paragraph continues with further explanation"),
        ]
    }
    analysis = {
        "cohort": {
            "inclusion_criteria": [
                fact("adults", "Patients aged 21 years or older"),
                fact("no kidney cancer", "A history of kidney cancer on or before the index date."),
            ],
            "exclusion_criteria": [fact("Maintenance dialysis", "Maintenance dialysis on or before")],
        }
    }
    graded = evaluation.grade_protocol(protocol, answer, analysis, kind_fields=kinds)
    # The reader wrote 16 beside a quote that says 18: found, not correct. '12' and 'index' are stated by
    # one of the two facts of the second item.
    assert graded["answer_found"] == 2 and graded["answer_correct"] == 1 and graded["wrong_value"] == ["I1"]
    assert graded["extra_in_answer"] == ["The study runs in three countries"]
    # In the analysis the age is given as 21, and the kidney-cancer exclusion stands among the inclusions
    # 'adults' states no number, so its quote speaks for it: 21, not 18
    assert graded["analysis_found"] == 3 and graded["analysis_correct"] == 2
    right = {"answers": [fact("adults", "Patients aged 18 years or older at the index date.")]}
    assert evaluation.grade_protocol(protocol, right, None)["answer_correct"] == 1
    assert graded["misplaced"] == ["E1"] and graded["extra_in_analysis"] == []
    wrong = {"answers": [fact("Patients aged 16 years or older", "Patients aged sixteen years or older")]}
    assert evaluation.grade_protocol(protocol, wrong, None)["wrong_value"] == ["I1"]


def test_a_required_value_that_the_page_does_not_hold_fails_the_check():
    bad = reference()
    bad["items"][0]["values"] = ["18", "21"]
    problems = evaluation.check_protocol(bad, protocol_pdf(), QUESTION)["items"][0]["problems"]
    assert problems == ["value '21' is not on page 1"]


def test_the_question_search_uses_the_configured_budget_and_skips_only_the_hits_beyond_it(
    settings, monkeypatch
):
    """research_protocol leaves out the text of a hit that does not fit and goes on with the shorter hits
    after it (exploration.Explorer.search); the check counts the same way, with the configured budget."""
    import dataclasses

    chunks = sections(extract_pages(protocol_pdf()))
    inclusion, exclusion = (next(c["section_id"] for c in chunks if c["page"] == n) for n in (1, 2))
    hits = [
        {"section_id": inclusion, "text": "w" * 100},
        {"section_id": "other", "text": "x" * 900},
        {"section_id": exclusion, "text": "y" * 500},
    ]
    monkeypatch.setattr(evaluation, "search_sections", lambda *args: {"results": [dict(h) for h in hits]})
    folder = evaluation.archive_dir(settings)
    folder.mkdir(parents=True)
    (folder / (evaluation.protocol_id(reference()) + ".pdf")).write_bytes(protocol_pdf())
    gold = {"question": QUESTION, "protocols": [reference()]}
    assert evaluation.check(dataclasses.replace(settings, research_budget_chars=1000), gold, True) == 1
    assert evaluation.check(dataclasses.replace(settings, research_budget_chars=1600), gold, True) == 0
    hits.insert(2, {"section_id": "long", "text": "z" * 5000})
    assert evaluation.check(dataclasses.replace(settings, research_budget_chars=1600), gold, True) == 0


def test_grading_closes_the_service_it_opens(settings, monkeypatch):
    import asyncio

    closed = []

    async def close(self):
        closed.append(True)

    monkeypatch.setattr(evaluation.Service, "close", close)
    assert asyncio.run(evaluation.grade(settings, evaluation.load())) == 1
    assert closed == [True]


def test_a_required_value_is_a_whole_number_or_code_not_part_of_another():
    """'3' is not stated by '13 patients', '18' not by '2018' or '180'; a code is stated by its subcodes."""
    protocol = reference()
    protocol["items"][0]["values"] = ["18", "3|three"]
    protocol["items"][2]["values"] = ["C25"]
    answer = {
        "answers": [
            fact("aged 18 years or older, 13 patients", "Patients aged 18 years or older at the index date."),
            fact("kidney cancer C25.1", "A history of kidney cancer on or before the index date."),
        ]
    }
    graded = evaluation.grade_protocol(protocol, answer, None)
    assert graded["wrong_value"] == ["I1"] and graded["answer_correct"] == 1
    assert not evaluation.states("18", "since 2018") and not evaluation.states("18", "180 days")
    assert evaluation.states("E11", "E110") and not evaluation.states("E11", "ICE11")
    assert evaluation.states("18", "≥18 years") and evaluation.states("3|three", "three months")
    # The survival years of study 105807: '12 months and 3 years' or 'I21, I23' do not state 1, 2 and 3
    survival = next(
        i
        for gold in evaluation.load_all()
        for p in gold["protocols"]
        for i in p["items"]
        if i["label"] == "survival rate at 1, 2 and 3 years"
    )
    (years,) = survival["values"]
    assert evaluation.states(years, "survival at 1, 2 and 3 years") and evaluation.states(
        years, "1-, 2- and 3-year"
    )
    assert not evaluation.states(years, "12 months and 3 years") and not evaluation.states(years, "I21, I23")


def test_the_pre_push_hook_skips_a_push_that_only_deletes(tmp_path):
    import os
    import shutil
    import subprocess

    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no POSIX shell")
    marker = tmp_path / "ran"
    (tmp_path / "uv").write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    (tmp_path / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    zero = "0" * 40

    def push(lines: str) -> bool:
        marker.unlink(missing_ok=True)
        subprocess.run(
            [shell, str(ROOT / ".githooks" / "pre-push")],
            input=lines,
            text=True,
            env=env,
            cwd=ROOT,
            check=True,
        )
        return marker.exists()

    assert not push(f"(delete) {zero} refs/heads/x {'a' * 40}\n")
    assert push(f"refs/heads/x {'b' * 40} refs/heads/x {zero}\n")
    assert push(f"(delete) {zero} refs/heads/x {'a' * 40}\nrefs/heads/y {'b' * 40} refs/heads/y {zero}\n")
    assert push("")
