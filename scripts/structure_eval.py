"""Reproducible evaluation of protocol structure detection over local PDFs (the PDFs are not redistributed).

    uv run scripts/structure_eval.py split               # write tests/fixtures/structure/split.json
    uv run scripts/structure_eval.py measure OUT.json [--split dev|eval|all]
    uv run scripts/structure_eval.py compare BEFORE.json AFTER.json

`split` assigns every PDF under data/protocols to dev or eval by template family, so one family never
sits on both sides: DARWIN EU studies share one template; otherwise the running page header's first
distinctive word stands for the producer's template, else the study is its own family.

`measure` records, per PDF (with its SHA-256) and with every denominator:
- weak-label headings: on PDFs with bookmarks, headings detected from the text alone (outline ignored)
  against bookmark titles printed as a line on their page. Detected lines no bookmark names are
  reported as unsupported, not as errors: bookmarks are incomplete.
- template chapters: on PDFs without bookmarks (a contents-page outline may still be used), numbered lines naming an EMA PASS template chapter
  (EMA/623947/2012) and whether they are detected as headings.
- reading set: characters read per page, reference-list-like chunks read (a proxy, see below) and
  ENCePP questionnaire text read.
- labels (tests/fixtures/structure/labels.json): labelled key passages (definitions, code lists, data
  sources, cohort criteria) that reach the reading set, labelled headings detected, labelled checklist
  ranges excluded.
- evidence: every quote of the reference extractions in data/comparisons revalidated.
The reference-list proxy (a read chunk with five or more "et al") also counts data-source annexes that
cite papers; it is a signal for review, not a correctness measure.
"""

import argparse
import glob
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf
from pydantic import ValidationError

from ema_rwe.domain import Extraction, RWEError
from ema_rwe.pdf import (
    PARSER_VERSION,
    extract_pages,
    normalize_quote,
    reading_order,
    sections,
    validate_evidence,
)
from ema_rwe.vocabulary import canonical

PDF_DIR = ROOT / "data" / "protocols"
FIXTURES = ROOT / "tests" / "fixtures" / "structure"
TEMPLATE_CHAPTERS = {
    "responsible parties",
    "abstract",
    "amendments and updates",
    "milestones",
    "rationale and background",
    "research question and objectives",
    "research methods",
    "protection of human subjects",
    "management and reporting of adverse events adverse reactions",
    "plans for disseminating and communicating study results",
    "references",
}
NUMBERED = re.compile(r"^\s*((?:\d{1,2}|[IVX]{1,4})(?:\.\d{1,2})*)\.?\s+(.{3,100}?)\s*$")
GENERIC_HEADER_WORDS = {
    "page",
    "protocol",
    "study",
    "confidential",
    "version",
    "final",
    "draft",
    "date",
    "non",
    "interventional",
    "post",
    "authorisation",
    "authorization",
    "safety",
    "observational",
    "clean",
    "amendment",
    "document",
    "number",
}


def pdf_files() -> list[Path]:
    return sorted(Path(p) for p in glob.glob(str(PDF_DIR / "pdf_*.pdf")))


def study_of(path: Path) -> str:
    return path.name.split("_")[1]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def darwin_studies() -> set[str]:
    db = sqlite3.connect(f"file:{ROOT / 'data' / 'ema.sqlite3'}?mode=ro", uri=True)
    rows = db.execute("SELECT id, json_extract(body, '$.darwin_eu') FROM studies").fetchall()
    return {sid for sid, flag in rows if flag}


def family(study_id: str, pages, darwin: set[str]) -> str:
    if study_id in darwin:
        return "darwin-eu"
    firsts = []
    for page in pages:
        line = next((l for l in page.text.splitlines() if l.strip()), "")
        words = [w for w in re.findall(r"[a-z]{4,}", line.casefold()) if w not in GENERIC_HEADER_WORDS]
        firsts.append(words[0] if words else None)
    word, count = Counter(w for w in firsts if w).most_common(1)[0] if any(firsts) else (None, 0)
    return f"header:{word}" if word and count >= len(pages) / 2 else f"study:{study_id}"


def cmd_split(_args) -> None:
    darwin = darwin_studies()
    split = {}
    for path in pdf_files():
        data = path.read_bytes()
        try:
            pages = extract_pages(data)
        except RWEError as exc:
            split[path.name] = {"study_id": study_of(path), "sha256": sha256(data), "error": exc.code}
            continue
        fam = family(study_of(path), pages, darwin)
        side = "dev" if int(hashlib.sha256(fam.encode()).hexdigest(), 16) % 2 == 0 else "eval"
        split[path.name] = {"study_id": study_of(path), "sha256": sha256(data), "family": fam, "split": side}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "split.json").write_text(json.dumps(split, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    sides = Counter(v.get("split", "error") for v in split.values())
    families = Counter(v["family"] for v in split.values() if "family" in v)
    print(json.dumps({"pdfs": len(split), "sides": sides, "largest_families": families.most_common(8)}))


def heading_lines(chunks) -> set[tuple[int, str]]:
    """(page, heading) for chunks whose heading is printed on that page (not carried over)."""
    return {
        (c["page"], canonical(c["section"]))
        for c in chunks
        if c["section"] and normalize_quote(c["section"]) in normalize_quote(c["text"])
    }


def weak_label_headings(data: bytes, pages) -> dict:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        toc = doc.get_toc()
    lines = {p.page: {canonical(l) for l in p.text.splitlines() if l.strip()} for p in pages}
    gold, gold_top = set(), set()
    for level, title, page in toc:
        key = (page, canonical(" ".join(str(title).split())))
        if key[1] and key[1] in lines.get(page, set()):
            gold.add(key)
            if level == 1:
                gold_top.add(key)
    detected = heading_lines(sections(extract_pages(data, use_outline=False)))
    return {
        "gold": len(gold),
        "gold_detected": len(gold & detected),
        "gold_top": len(gold_top),
        "gold_top_detected": len(gold_top & detected),
        "detected": len(detected),
        "detected_unsupported": len(detected - gold),
        "missed": sorted(t for _, t in gold - detected)[:40],
    }


def template_chapters(pages, chunks) -> dict:
    detected = heading_lines(chunks)
    rows = []
    for page in pages:
        if re.search(r"\.{4,}\s*\d+", page.text):  # table-of-contents page
            continue
        for line in page.text.splitlines():
            m = NUMBERED.match(line)
            if m and canonical(m.group(2)) in TEMPLATE_CHAPTERS:
                rows.append((canonical(m.group(2)), (page.page, canonical(line.strip())) in detected))
    return {
        "lines": len(rows),
        "detected": sum(ok for _, ok in rows),
        "missed": sorted(t for t, ok in rows if not ok),
    }


def questionnaire_pages(pages) -> set[int]:
    return {
        p.page
        for p in pages
        if re.search(r"encepp|checklist", p.text, re.IGNORECASE)
        and sum(l.rstrip().endswith("?") for l in p.text.splitlines()) >= 3
    }


def label_checks(label: dict, pages, chunks, read) -> dict:
    read_ids = {c["section_id"] for c in read}
    passages = []
    for passage in label.get("key_passages", []):
        quote = normalize_quote(passage["quote"])
        hits = [c for c in chunks if c["page"] == passage["page"] and quote in normalize_quote(c["text"])]
        passages.append(
            {
                **passage,
                "found": bool(hits),
                "read": any(c["section_id"] in read_ids for c in hits),
            }
        )
    detected = heading_lines(chunks)
    headings = [
        {**h, "detected": (h["page"], canonical(h["text"])) in detected} for h in label.get("headings", [])
    ]
    checklist = None
    if label.get("checklist"):
        span = label["checklist"]
        quote = normalize_quote(span["start_text"])
        # The checklist starts at the chunk holding its start text; text before it on that page is not part of it.
        start = next(
            (
                c["section_id"]
                for c in chunks
                if c["page"] == span["start_page"] and quote in normalize_quote(c["text"])
            ),
            "",
        )
        inside = [
            c
            for c in chunks
            if span["start_page"] <= c["page"] <= span["end_page"] and c["section_id"] >= start
        ]
        checklist = {
            "chunks": len(inside),
            "read_chunks": sum(c["section_id"] in read_ids for c in inside),
            "read_chars": sum(len(c["text"]) for c in inside if c["section_id"] in read_ids),
        }
    return {"passages": passages, "headings": headings, "checklist": checklist}


def reference_extractions() -> dict[str, dict]:
    refs = {}
    for f in sorted(glob.glob(str(ROOT / "data" / "comparisons" / "*" / "study_*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if d.get("analysis") and (d.get("source") or {}).get("local_filename"):
            refs.setdefault(d["source"]["local_filename"], d)
    return refs


def evidence_results(ref: dict, pages) -> Counter:
    """Revalidate each quote of a stored extraction on its own, under its field's rules."""
    chunks = sections(pages)
    results = Counter()
    for name, value in ref["analysis"].items():
        if name in {"schema_version", "missing_information"} or not value:
            continue
        blocks = [(name, value)] if name != "cohort" else [("cohort", value)]
        for field, block in blocks:
            facts = []
            if field == "cohort":
                for sub, v in block.items():
                    if sub == "design_schema" and v:
                        facts += [
                            ({"cohort": {"design_schema": {"time_windows": [f]}}}, f)
                            for f in v.get("time_windows") or []
                        ]
                    elif isinstance(v, list):
                        facts += [({"cohort": {sub: [f]}}, f) for f in v]
                    elif v:
                        facts.append(({"cohort": {sub: v}}, v))
            elif isinstance(block, list):
                facts = [({field: [f]}, f) for f in block]
            else:
                facts = [({field: block}, block)]
            for payload, fact in facts:
                for ev in fact.get("evidence") or []:
                    single = json.loads(json.dumps(payload))
                    target = single
                    while isinstance(target, dict) and "evidence" not in target:
                        inner = next(iter(target.values()))
                        target = inner[0] if isinstance(inner, list) else inner
                    target["evidence"] = [ev]
                    try:
                        validate_evidence(Extraction.model_validate(single), pages, chunks)
                        results["ok"] += 1
                    except ValidationError:
                        results["model_invalid"] += 1
                    except RWEError as exc:
                        results[exc.code] += 1
    return results


def commit() -> str:
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "src"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return head + ("+dirty" if dirty else "")


def cmd_measure(args) -> None:
    code = commit()  # before the run: sources may change while hundreds of PDFs are measured
    split = json.loads((FIXTURES / "split.json").read_text(encoding="utf-8"))
    labels_path = FIXTURES / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    labels_by_sha = {l["sha256"]: l for l in labels.get("protocols", [])}
    refs = reference_extractions()
    per_pdf = {}
    for path in pdf_files():
        info = split.get(path.name)
        if not info or "split" not in info or (args.split != "all" and info["split"] != args.split):
            continue
        data = path.read_bytes()
        if sha256(data) != info["sha256"]:
            per_pdf[path.name] = {"error": "sha256 differs from split.json"}
            continue
        pages = extract_pages(data)
        chunks = sections(pages)
        read = reading_order(chunks)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            has_bookmarks = bool(doc.get_toc())
        questionnaire = questionnaire_pages(pages)
        page_chars = Counter()
        for c in read:
            page_chars[c["page"]] += len(c["text"])
        row = {
            "study_id": info["study_id"],
            "sha256": info["sha256"],
            "family": info["family"],
            "pages": len(pages),
            "has_bookmarks": has_bookmarks,
            "outline_source": next((s for p in pages if (s := getattr(p, "outline_source", None))), None),
            "read_chars": sum(page_chars.values()),
            "total_chars": sum(len(p.text) for p in pages),
            "read_chars_by_page": dict(sorted(page_chars.items())),
            "roles": dict(Counter(c["role"] for c in chunks)),
            "reference_like_read_chars": sum(
                len(c["text"]) for c in read if len(re.findall(r"\bet al\b", c["text"])) >= 5
            ),
            "questionnaire_pages": len(questionnaire),
            "questionnaire_read_chars": sum(len(c["text"]) for c in read if c["page"] in questionnaire),
        }
        if has_bookmarks:
            row["weak_label_headings"] = weak_label_headings(data, pages)
        else:
            row["template_chapters"] = template_chapters(pages, chunks)
        if info["sha256"] in labels_by_sha:
            row["labels"] = label_checks(labels_by_sha[info["sha256"]], pages, chunks, read)
        if path.name in refs:
            row["evidence"] = dict(evidence_results(refs[path.name], pages))
        per_pdf[path.name] = row
    report = {
        "parser_version": PARSER_VERSION,
        "code": code,
        "split": args.split,
        "pdfs": len(per_pdf),
        "totals": totals(per_pdf),
        "per_pdf": per_pdf,
    }
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("parser_version", "code", "split", "pdfs", "totals")}, indent=1))


def totals(per_pdf: dict) -> dict:
    rows = [r for r in per_pdf.values() if "error" not in r]
    t = Counter()
    for r in rows:
        t["read_chars"] += r["read_chars"]
        t["total_chars"] += r["total_chars"]
        t["reference_like_read_chars"] += r["reference_like_read_chars"]
        t["questionnaire_pdfs"] += bool(r["questionnaire_pages"])
        t["questionnaire_read_chars"] += r["questionnaire_read_chars"]
        t["questionnaire_pdfs_with_read_text"] += r["questionnaire_read_chars"] > 0
        for key, value in (r.get("weak_label_headings") or {}).items():
            if isinstance(value, int):
                t[f"weak_{key}"] += value
        if tc := r.get("template_chapters"):
            t["template_lines"] += tc["lines"]
            t["template_detected"] += tc["detected"]
        if lab := r.get("labels"):
            t["label_passages"] += len(lab["passages"])
            t["label_passages_read"] += sum(p["read"] for p in lab["passages"])
            t["label_headings"] += len(lab["headings"])
            t["label_headings_detected"] += sum(h["detected"] for h in lab["headings"])
            if lab["checklist"]:
                t["label_checklists"] += 1
                t["label_checklist_read_chars"] += lab["checklist"]["read_chars"]
        for key, value in (r.get("evidence") or {}).items():
            t[f"evidence_{key}"] += value
    t["pdfs_with_bookmarks"] = sum(r["has_bookmarks"] for r in rows)
    t["pdfs_with_contents_outline"] = sum(r.get("outline_source") == "toc" for r in rows)
    return dict(sorted(t.items()))


def cmd_compare(args) -> None:
    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.before, args.after))
    print(
        f"before {a['code']} ({a['parser_version']})  after {b['code']} ({b['parser_version']})  split {a['split']}/{b['split']}"
    )
    for key in sorted(set(a["totals"]) | set(b["totals"])):
        x, y = a["totals"].get(key, 0), b["totals"].get(key, 0)
        print(f"  {key:40s} {x:>12} -> {y:>12}{'' if x == y else '   *'}")
    changed = []
    for name in sorted(set(a["per_pdf"]) & set(b["per_pdf"])):
        pa = {int(k): v for k, v in a["per_pdf"][name].get("read_chars_by_page", {}).items()}
        pb = {int(k): v for k, v in b["per_pdf"][name].get("read_chars_by_page", {}).items()}
        gained = {p: pb.get(p, 0) - pa.get(p, 0) for p in set(pa) | set(pb) if pb.get(p, 0) > pa.get(p, 0)}
        lost = {p: pa.get(p, 0) - pb.get(p, 0) for p in set(pa) | set(pb) if pa.get(p, 0) > pb.get(p, 0)}
        if gained or lost:
            changed.append((name, sum(gained.values()), sum(lost.values()), sorted(gained), sorted(lost)))
        la, lb = a["per_pdf"][name].get("labels"), b["per_pdf"][name].get("labels")
        if la and lb:
            for pa_, pb_ in zip(la["passages"], lb["passages"]):
                if pa_["read"] != pb_["read"]:
                    print(
                        f"  LABEL {name} p.{pb_['page']} {pb_['kind']}: read {pa_['read']} -> {pb_['read']}"
                    )
    print(f"PDFs whose reading set changed: {len(changed)} of {len(set(a['per_pdf']) & set(b['per_pdf']))}")
    for name, g, l, gp, lp in sorted(changed, key=lambda c: -(c[1] + c[2]))[: args.limit]:
        print(f"  {name[:40]} +{g} -{l} chars; pages gained {gp[:12]} lost {lp[:12]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("split")
    m = sub.add_parser("measure")
    m.add_argument("out")
    m.add_argument("--split", choices=("dev", "eval", "all"), default="all")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    {"split": cmd_split, "measure": cmd_measure, "compare": cmd_compare}[args.command](args)


if __name__ == "__main__":
    main()
