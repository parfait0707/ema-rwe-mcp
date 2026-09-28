"""Quasi-gold set for catalogue search: pool candidates from several strategies, then measure recall.

    uv run scripts/gold_eval.py pool [--db PATH]   # write tests/fixtures/gold/pool/qNN.csv for labelling
    uv run scripts/gold_eval.py merge FILE...     # copy LLM pre-labels (JSON) into the pool CSVs
    uv run scripts/gold_eval.py eval [--db PATH]   # relative recall of each strategy against the labels

Each question (tests/fixtures/gold/questions.json) has concept blocks. A strategy retrieves a ranked
list per block; blocks are intersected (AND). The pool is the union of every strategy's top
POOL_DEPTH studies, labelled relevant / partial / not_relevant / unclear from catalogue metadata.
Relative recall = relevant studies a strategy retrieves / relevant studies in the pool (TREC-style
pooling: studies no strategy found are unknown, so this compares strategies, not absolute recall).
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ema_rwe.domain import CodeCandidate
from ema_rwe.storage import Repository
from ema_rwe.vocabulary import canonical

GOLD = ROOT / "tests" / "fixtures" / "gold"
POOL_DEPTH = 60  # per strategy, as in TREC pooling; narrow strategies rarely reach it
RRF_K = 20  # TrialGPT's fusion constant
STRATEGIES = ("variants", "variants_any_role", "codes_only", "seed", "alt_variants", "facet")
LABELS = ("relevant", "partial", "not_relevant", "unclear")
FIELDS = (
    "question_id",
    "study_id",
    "found_by",
    "title",
    "conditions",
    "outcomes",
    "exposures",
    "objective",
    "description",
    "label_llm",
    "confidence",
    "reason",
    "evidence_field",
    "evidence_quote",
    "label_final",
)


def rrf(ranked_lists: list[list[str]]) -> dict[str, float]:
    """Reciprocal rank fusion score per study id."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, study_id in enumerate(ranked, 1):
            scores[study_id] = scores.get(study_id, 0.0) + 1 / (RRF_K + rank)
    return scores


def intersect(block_scores: list[dict[str, float]]) -> list[str]:
    """Studies present in every block, ordered by their summed fusion score (AND across blocks)."""
    common = set(block_scores[0]).intersection(*block_scores[1:])
    return sorted(common, key=lambda s: (-sum(b[s] for b in block_scores), s))


def search_ids(repo: Repository, query: str, role: str, codes=None) -> list[str]:
    rows = repo.search(query, None, False, None, codes=codes, role=role, split_long=False)
    return [r["study_id"] for r in rows]


def code_lists(repo: Repository, block: dict, role: str) -> list[list[str]]:
    lists = []
    for system, code in block["codes"]:
        candidate = CodeCandidate(system=system, code=code)
        lists.append(search_ids(repo, code, role, [candidate]))
    return lists


def facet_ids(repo: Repository, terms: list[str]) -> list[str]:
    wanted = [canonical(t) for t in terms]
    with repo.connection() as db:
        rows = db.execute("SELECT id, body FROM studies ORDER BY id").fetchall()
    return [
        study_id
        for study_id, body in rows
        if any(w in canonical(c) for c in json.loads(body).get("conditions", []) for w in wanted)
    ]


def block_ranking(repo: Repository, block: dict, strategy: str, alt: list[str] | None) -> dict[str, float]:
    role = "any" if block["role"] == "any" else block["role"]
    if strategy == "variants":
        lists = [search_ids(repo, v, role) for v in block["variants"]] + code_lists(repo, block, role)
    elif strategy == "variants_any_role":
        lists = [search_ids(repo, v, "any") for v in block["variants"]] + code_lists(repo, block, "any")
    elif strategy == "codes_only":
        lists = code_lists(repo, block, "any")
    elif strategy == "seed":
        lists = [search_ids(repo, block["seed"], "any")]
    elif strategy == "alt_variants":
        lists = [search_ids(repo, v, role) for v in alt or []]
    elif strategy == "facet":
        lists = [facet_ids(repo, block["facet"])] if block["facet"] else []
    else:
        raise ValueError(strategy)
    return rrf(lists)


def retrieve(repo: Repository, question: dict, strategy: str, alt: dict) -> list[str]:
    """One strategy's ranked result for a question; a block without terms for it yields nothing."""
    blocks = [
        block_ranking(repo, b, strategy, (alt.get(question["id"]) or [None] * 9)[i])
        for i, b in enumerate(question["blocks"])
    ]
    return intersect(blocks) if all(blocks) else []


def load_questions() -> tuple[list[dict], dict]:
    questions = json.loads((GOLD / "questions.json").read_text(encoding="utf-8"))["questions"]
    alt_path = GOLD / "alt_variants.json"
    alt = json.loads(alt_path.read_text(encoding="utf-8")) if alt_path.exists() else {}
    return questions, alt


def pool(repo: Repository) -> None:
    questions, alt = load_questions()
    out = GOLD / "pool"
    out.mkdir(exist_ok=True)
    with repo.connection() as db:
        bodies = {i: json.loads(b) for i, b in db.execute("SELECT id, body FROM studies")}
    for q in questions:
        found: dict[str, list[str]] = {}
        for strategy in STRATEGIES:
            for study_id in retrieve(repo, q, strategy, alt)[:POOL_DEPTH]:
                found.setdefault(study_id, []).append(strategy)
        path = out / f"{q['id']}.csv"
        previous = {r["study_id"]: r for r in read_csv(path)} if path.exists() else {}
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, FIELDS, lineterminator="\n")
            writer.writeheader()
            for study_id in sorted(found, key=lambda s: (-len(found[s]), s)):
                s = bodies[study_id]
                row = {
                    "question_id": q["id"],
                    "study_id": study_id,
                    "found_by": ";".join(found[study_id]),
                    "title": s.get("title", ""),
                    "conditions": "; ".join(s.get("conditions", [])),
                    "outcomes": (s.get("outcomes") or "")[:800],
                    "exposures": "; ".join(s.get("exposures", [])[:15]),
                    "objective": (s.get("objective") or "")[:600],
                    "description": (s.get("description") or "")[:600],
                }
                # Keep labels already given, so re-pooling never discards review work.
                old = previous.get(study_id, {})
                row.update({k: old.get(k, "") for k in FIELDS if k not in row})
                writer.writerow(row)
        print(f"{q['id']}: {len(found)} pooled")


REVIEW_ORDER = {"unclear": 0, "low": 1, "medium": 2, "high": 3}


def merge(files: list[Path]) -> None:
    """Copy LLM pre-labels into the pool CSVs, verifying each evidence quote against its field, and
    order rows for review: unclear and low-confidence labels first."""
    labels: dict[str, dict] = {}
    for path in files:
        for qid, studies in json.loads(path.read_text(encoding="utf-8")).items():
            labels.setdefault(qid, {}).update(studies)
    for qid, studies in sorted(labels.items()):
        path = GOLD / "pool" / f"{qid}.csv"
        rows = read_csv(path)
        missing, unverified = 0, 0
        for row in rows:
            given = studies.get(row["study_id"])
            if not given:
                missing += 1
                continue
            if given["label"] not in LABELS:
                raise SystemExit(f"{qid}/{row['study_id']}: unknown label {given['label']!r}")
            quote, field = given.get("evidence_quote") or "", given.get("evidence_field") or ""
            if given["label"] != "unclear" and quote not in row.get(field, ""):
                unverified += 1
                given = {
                    **given,
                    "confidence": "low",
                    "reason": "[quote not found] " + given.get("reason", ""),
                }
            row.update(
                label_llm=given["label"],
                confidence=given.get("confidence", ""),
                reason=given.get("reason", ""),
                evidence_field=field,
                evidence_quote=quote,
            )
        rows.sort(
            key=lambda r: (
                0 if r["label_llm"] == "unclear" else REVIEW_ORDER.get(r["confidence"], 0),
                -len(r["found_by"].split(";")),
                r["study_id"],
            )
        )
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)
        print(f"{qid}: {len(rows) - missing}/{len(rows)} labelled, {unverified} quotes not found")


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def labels_for(question_id: str) -> dict[str, str]:
    """Final label per pooled study: the reviewer's label_final, else the LLM pre-label."""
    rows = read_csv(GOLD / "pool" / f"{question_id}.csv")
    return {r["study_id"]: (r["label_final"] or r["label_llm"]) for r in rows}


def metrics(retrieved: list[str], labels: dict[str, str], accept: tuple[str, ...]) -> dict:
    """Relative recall and judged precision of one retrieved set against pooled labels."""
    relevant = {s for s, label in labels.items() if label in accept}
    hits = [s for s in retrieved if s in relevant]
    judged = [s for s in retrieved if s in labels]
    return {
        "retrieved": len(retrieved),
        "relevant_retrieved": len(hits),
        "relevant_in_pool": len(relevant),
        "relative_recall": round(len(hits) / len(relevant), 3) if relevant else None,
        "judged_precision": round(len(hits) / len(judged), 3) if judged else None,
        "unjudged": len(retrieved) - len(judged),
    }


def evaluate(repo: Repository) -> dict:
    questions, alt = load_questions()
    report = {}
    for q in questions:
        labels = labels_for(q["id"])
        if not all(v in LABELS for v in labels.values()):
            raise SystemExit(f"{q['id']}: unlabelled or unknown labels in the pool")
        report[q["id"]] = {}
        for strategy in STRATEGIES:
            retrieved = retrieve(repo, q, strategy, alt)
            report[q["id"]][strategy] = {
                "strict": metrics(retrieved, labels, ("relevant",)),
                "lenient": metrics(retrieved, labels, ("relevant", "partial")),
            }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["pool", "merge", "eval"])
    parser.add_argument("files", nargs="*", type=Path, help="label JSON files for merge")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ema.sqlite3")
    args = parser.parse_args()
    if args.command == "merge":
        merge(args.files)
        return
    repo = Repository(args.db)
    if args.command == "pool":
        pool(repo)
    else:
        print(json.dumps(evaluate(repo), indent=1))


if __name__ == "__main__":
    main()
