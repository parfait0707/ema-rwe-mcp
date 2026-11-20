"""Reference items for protocol extraction, and the checks made against them.

    uv run python scripts/extraction_eval.py check [--require]   # no LLM, no network: run on every change
    uv run python scripts/extraction_eval.py fetch               # get the reference protocols once (EMA)
    uv run python scripts/extraction_eval.py catalogue [--online]  # after a catalogue update
    uv run python scripts/extraction_eval.py grade               # grade the extractions saved in the database
    uv run python scripts/extraction_eval.py run                 # extract with the configured LLM, then grade

The reference (tests/fixtures/extraction_gold/eligibility.json) lists, for four protocols, every eligibility
criterion as read by hand: a description in our own words, its physical page and a short pattern that finds
the passage. It holds no protocol text; quotes are taken from the user's own copy of the PDF when a check runs.

check    What the server decides, for every reference item: the passage is found on its page, lies in a
         section that analyze_protocol serves to the reader (not background, references, contents or a
         checklist), comes back from research_protocol's search for the question, is accepted as evidence,
         and a quote given one page off is moved back. A change to the PDF parser, the section roles, the
         reading order or the evidence rules that loses a criterion fails here. Protocols that are not in the
         archive are reported and, with --require, fail the run.
grade    What a reader (the calling LLM or the configured one) extracted: how many reference items its saved
         answer to the reference question and its cohort block contain, against the minimum in the reference.
catalogue  That the reference studies are still in the catalogue as non-interventional studies; --online also
         asks EMA whether their latest protocol is still the document the reference was read from.

Scope: eligibility criteria only, of four English protocols with a text layer. Data sources, outcomes and
code lists are not graded. See tests/fixtures/extraction_gold/README.md.
"""

import argparse
import asyncio
import dataclasses
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ONLINE_INTERVAL_SECONDS = 30
sys.path.insert(0, str(ROOT / "src"))

from ema_rwe.config import Settings
from ema_rwe.domain import Evidence, Extraction, Fact, RWEError
from ema_rwe.ema import is_non_interventional
from ema_rwe.llm import configured
from ema_rwe.pdf import (
    EVIDENCE_REJECT_ROLES,
    extract_pages,
    normalize_quote,
    reading_order,
    search_sections,
    sections,
    validate_evidence,
    with_corrected_pages,
)
from ema_rwe.service import Service

GOLD = ROOT / "tests" / "fixtures" / "extraction_gold" / "eligibility.json"


def load(path: Path = GOLD) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def archive_dir(settings: Settings) -> Path:
    return settings.protocol_dir or settings.db_path.parent / "protocols"


def protocol_id(protocol: dict) -> str:
    return f"pdf_{protocol['study_id']}_{protocol['sha256']}"


def matches(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, re.sub(r"\s+", " ", text), re.IGNORECASE))


def passage(pages: list, item: dict) -> str | None:
    """The line of the item's page that its pattern finds, with the next line when it is too short to be
    evidence on its own; None when the page does not hold it."""
    page = next((p for p in pages if p.page == item["page"]), None)
    lines = [line.strip() for line in page.text.splitlines() if line.strip()] if page else []
    for index, line in enumerate(lines):
        if matches(item["pattern"], line):
            return line if len(normalize_quote(line)) >= 8 else " ".join(lines[index : index + 2])
    return None


def check_protocol(protocol: dict, data: bytes, question: str, budget: int = 40000) -> dict:
    """Every reference item against what the server would do with this PDF. Returns
    {items: [{id, problems: [...]}], pages}; an item without problems passed."""
    pages = extract_pages(data)
    chunks = sections(pages)
    served = {c["section_id"] for c in reading_order(chunks)}
    # research_protocol's first answer to the caller: question-ranked sections within the budget
    ranked, used, offered = search_sections(chunks, question, 30)["results"], 0, set()
    for hit in ranked:
        if used and used + len(hit["text"]) > budget:
            break
        used += len(hit["text"])
        offered.add(hit["section_id"])
    results = []
    for item in protocol["items"]:
        problems = []
        quote = passage(pages, item)
        if quote is None:
            results.append({"id": item["id"], "problems": [f"not found on page {item['page']}"]})
            continue
        inside = normalize_quote(quote)
        holding = [c for c in chunks if c["page"] == item["page"] and inside in normalize_quote(c["text"])]
        if not holding:
            problems.append("split across sections")
        elif not any(c["section_id"] in served for c in holding):
            problems.append(f"not served to the reader (role {holding[0]['role']})")
        elif all(c["role"] in EVIDENCE_REJECT_ROLES for c in holding):
            problems.append(f"in a section whose evidence is refused (role {holding[0]['role']})")
        if holding and not any(c["section_id"] in offered for c in holding):
            problems.append("not in the question search within the budget")
        fact = Fact(value=item["label"], evidence=[Evidence(page=item["page"], quote=quote)])
        try:
            validate_evidence(Extraction(key_notes=[fact]), pages, chunks)
        except RWEError as exc:
            problems.append(f"evidence refused: {exc.message}")
        # A reader that counts the page one off: the server moves the quote back
        off = Fact(value=item["label"], evidence=[Evidence(page=item["page"] + 1, quote=quote)])
        moved = with_corrected_pages(Extraction(key_notes=[off]), pages).key_notes[0].evidence[0]
        elsewhere = [p.page for p in pages if inside in normalize_quote(p.text)]
        if moved.page != item["page"] and elsewhere == [item["page"]]:
            problems.append(f"a quote given on page {item['page'] + 1} was not moved back")
        results.append({"id": item["id"], "problems": problems})
    return {"items": results, "pages": len(pages)}


def check(settings: Settings, gold: dict, require: bool) -> int:
    """Run check_protocol for every reference protocol in the archive; print a table; return the exit code."""
    failed = missing = checked = 0
    for protocol in gold["protocols"]:
        path = archive_dir(settings) / (protocol_id(protocol) + ".pdf")
        if not path.is_file():
            missing += 1
            print(f"  study {protocol['study_id']}: protocol not in the archive ({path.name})")
            continue
        result = check_protocol(protocol, path.read_bytes(), gold["question"])
        bad = [i for i in result["items"] if i["problems"]]
        if result["pages"] != protocol["pages"]:
            bad.append(
                {"id": "pages", "problems": [f"{result['pages']} pages, reference has {protocol['pages']}"]}
            )
        checked += 1
        failed += len(bad)
        print(
            f"  study {protocol['study_id']}: {len(result['items']) - len(bad)}/{len(result['items'])} items pass"
        )
        for item in bad:
            print(f"    {item['id']}: {'; '.join(item['problems'])}")
    total = len(gold["protocols"])
    print(f"extraction reference: {checked}/{total} protocols checked, {failed} item(s) failing")
    if missing:
        print(
            f"  {missing} reference protocol(s) are not in {archive_dir(settings)}: "
            "run `uv run python scripts/extraction_eval.py fetch` once (EMA, a few minutes)."
        )
    return 1 if failed or (require and missing) else 0


async def fetch(settings: Settings, gold: dict) -> int:
    """Download the reference protocols that are missing, one study at a time under the EMA request interval.
    A study whose latest protocol is another document than the reference is reported, not used."""
    service, status, asked = Service(settings), 0, 0
    try:
        for protocol in gold["protocols"]:
            path = archive_dir(settings) / (protocol_id(protocol) + ".pdf")
            if path.is_file():
                print(f"  study {protocol['study_id']}: present")
                continue
            if asked:
                await asyncio.sleep(ONLINE_INTERVAL_SECONDS)  # EMA answers a burst with 429
            asked += 1
            try:
                found = await service.get_protocol(protocol["study_id"])
            except RWEError as exc:
                print(f"  study {protocol['study_id']}: {exc.code}: {exc.message} (run fetch again later)")
                status = 1
                continue
            if found["download"]["sha256"] != protocol["sha256"]:
                print(
                    f"  study {protocol['study_id']}: the latest protocol is another document "
                    f"({found['protocol'].get('version')}, {found['download']['sha256'][:12]}) than the "
                    "reference: read it and revise the reference items."
                )
                status = 1
            else:
                print(f"  study {protocol['study_id']}: fetched")
    finally:
        await service.close()
    return status


async def catalogue(settings: Settings, gold: dict, online: bool) -> int:
    """The reference studies in the current catalogue; with online, whether EMA still lists the reference
    document as their latest protocol (one Study-documents request per study, no PDF)."""
    service, status, asked = Service(settings), 0, 0
    try:
        for protocol in gold["protocols"]:
            study = service.repo.get(protocol["study_id"])
            if study is None or not is_non_interventional(study.study_type):
                print(f"  study {protocol['study_id']}: not in the catalogue as a non-interventional study")
                status = 1
                continue
            note = "in the catalogue"
            if online:
                if asked:
                    await asyncio.sleep(ONLINE_INTERVAL_SECONDS)  # EMA answers a burst with 429
                asked += 1
                try:
                    found = await service.get_protocol(protocol["study_id"], download=False, refresh=True)
                    same = found["protocol"]["document_url"] == protocol["document_url"]
                    note += (
                        "; latest protocol unchanged"
                        if same
                        else "; LATEST PROTOCOL CHANGED: revise the reference"
                    )
                    status |= 0 if same else 1
                except RWEError as exc:
                    note += f"; could not ask EMA ({exc.code})"
                    status = 1
            print(f"  study {protocol['study_id']}: {note}")
    finally:
        await service.close()
    return status


def facts_text(fact: dict) -> tuple[str, str]:
    return fact["value"], " ".join(re.sub(r"\s+", " ", e["quote"]) for e in fact["evidence"])


def grade_protocol(protocol: dict, answer: dict | None, analysis: dict | None) -> dict:
    """How many reference items a saved answer and a saved cohort block contain. found: the item's pattern
    is in a fact's value or quotes; supported: in that fact's own verbatim quotes."""
    answers = [facts_text(f) for f in (answer or {}).get("answers", [])]
    cohort = (analysis or {}).get("cohort") or {}
    criteria = [
        facts_text(f) for key in ("inclusion_criteria", "exclusion_criteria") for f in cohort.get(key, [])
    ]
    rows = []
    for item in protocol["items"]:
        # A reader may word the criterion otherwise, or quote another of its lines: `accept` lists its key words.
        wanted = item.get("accept", item["pattern"])
        hits = [(value, quotes) for value, quotes in answers if matches(wanted, value + " " + quotes)]
        rows.append(
            {
                "id": item["id"],
                "answer_found": bool(hits),
                "answer_supported": any(matches(wanted, quotes) for _, quotes in hits),
                "cohort_found": any(matches(wanted, v + " " + q) for v, q in criteria),
            }
        )
    total = len(rows)
    return {
        "study_id": protocol["study_id"],
        "items": total,
        "answered": answer is not None,
        "analysed": analysis is not None,
        **{k: sum(r[k] for r in rows) for k in ("answer_found", "answer_supported", "cohort_found")},
        "missing": [r["id"] for r in rows if not r["answer_found"]],
    }


def grade(settings: Settings, gold: dict) -> int:
    """Grade what the database holds for the reference protocols and question."""
    service = Service(settings)
    minimum, status, graded = gold["minimum"], 0, 0
    print("  study     answer found/supported   cohort found   of")
    for protocol in gold["protocols"]:
        pid = protocol_id(protocol)
        saved = service.repo.analysis(protocol["study_id"])
        if saved and saved["source"].get("protocol_id") != pid:
            saved = None  # an analysis of another document than the reference
        with service.repo.connection() as db:
            row = db.execute(
                "SELECT body FROM protocol_answers WHERE cache_key=?",
                (service.explorer.key(pid, gold["question"], ""),),
            ).fetchone()
        answer = json.loads(row[0])["answer"] if row else None
        result = grade_protocol(protocol, answer, saved["analysis"] if saved else None)
        if not result["answered"] and not result["analysed"]:
            print(f"  {protocol['study_id']:>8}  nothing saved for this protocol")
            status = 1
            continue
        graded += 1
        low = []
        if result["answered"] and result["answer_found"] < minimum["answer_found"] * result["items"]:
            low.append("answer")
        if result["analysed"] and result["cohort_found"] < minimum["cohort_found"] * result["items"]:
            low.append("cohort")
        if not result["answered"] or not result["analysed"]:
            low.append("no answer saved" if not result["answered"] else "no analysis saved")
        status |= 1 if low else 0
        print(
            f"  {protocol['study_id']:>8}  {result['answer_found']:>12}/{result['answer_supported']:<11}"
            f"{result['cohort_found']:>13}   {result['items']}"
            + (
                f"   BELOW THE MINIMUM: {', '.join(low)}; not in the answer: {result['missing']}"
                if low
                else ""
            )
        )
    print(
        f"extraction grade: {graded}/{len(gold['protocols'])} protocols graded; minimum "
        f"{minimum['answer_found']:.0%} of the items in the answer, {minimum['cohort_found']:.0%} in the cohort block"
    )
    return status


async def run(settings: Settings, gold: dict) -> int:
    """Extract the reference protocols and answer the reference question with the configured LLM."""
    if not configured(settings):
        print(
            "No LLM is configured (LLM_MODEL with LLM_BACKEND=litellm or LLM_BASE_URL). With the caller route, ask "
            "your MCP client to run analyze_protocol with this question for each study, then run `grade`:\n"
            f"  question: {gold['question']}\n  studies: {', '.join(p['study_id'] for p in gold['protocols'])}"
        )
        return 1
    service = Service(settings)
    try:
        for protocol in gold["protocols"]:
            while (await service.analyze_protocol(protocol["study_id"]))["status"] == "extracting":
                pass  # each call waits LLM_WAIT_SECONDS for the running extraction
            await service.research_protocol(protocol_id(protocol), gold["question"], poll=False)
            print(f"  study {protocol['study_id']}: extracted and answered")
    finally:
        await service.close()
    return grade(settings, gold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["check", "fetch", "catalogue", "grade", "run"])
    parser.add_argument(
        "--require", action="store_true", help="check: fail when a reference protocol is missing"
    )
    parser.add_argument(
        "--online", action="store_true", help="catalogue: ask EMA for each study's latest protocol"
    )
    parser.add_argument("--gold", type=Path, default=GOLD)
    args = parser.parse_args()
    settings, gold = Settings(), load(args.gold)
    match args.command:
        case "check":
            return check(settings, gold, args.require)
        case "fetch" | "catalogue":
            if args.command == "catalogue" and not args.online:
                return asyncio.run(catalogue(settings, gold, False))
            # Asking EMA records what it answers: in a copy, so the catalogue file itself is not touched.
            # The PDFs go to the archive beside the real database.
            with tempfile.TemporaryDirectory() as work:
                copy = Path(work) / "ema.sqlite3"
                shutil.copyfile(settings.db_path, copy)
                online = dataclasses.replace(settings, db_path=copy, protocol_dir=archive_dir(settings))
                if args.command == "fetch":
                    return asyncio.run(fetch(online, gold))
                return asyncio.run(catalogue(online, gold, True))
        case "grade":
            return grade(settings, gold)
        case "run":
            return asyncio.run(run(settings, gold))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
