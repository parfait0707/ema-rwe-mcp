"""The quasi-gold evaluation helpers: fusion order, AND across blocks, and relative recall."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("gold_eval", ROOT / "scripts" / "gold_eval.py")
gold = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gold)


def test_rrf_rewards_studies_ranked_high_in_several_lists():
    scores = gold.rrf([["a", "b"], ["b", "c"]])
    assert max(scores, key=scores.get) == "b"
    assert scores["a"] == 1 / (gold.RRF_K + 1)


def test_blocks_are_intersected_and_ordered_by_summed_score():
    exposure = gold.rrf([["x", "y", "z"]])
    outcome = gold.rrf([["z", "y"]])
    # y scores 1/22 + 1/22, z scores 1/23 + 1/21 (slightly more), x is missing from the outcome block
    assert gold.intersect([exposure, outcome]) == ["z", "y"]
    assert gold.intersect([exposure]) == ["x", "y", "z"]


def test_relative_recall_counts_only_pooled_relevant_studies():
    labels = {"a": "relevant", "b": "partial", "c": "not_relevant", "d": "relevant"}
    strict = gold.metrics(["a", "b", "e"], labels, ("relevant",))
    assert strict["relevant_in_pool"] == 2 and strict["relative_recall"] == 0.5
    assert strict["unjudged"] == 1 and strict["judged_precision"] == 0.5
    lenient = gold.metrics(["a", "b", "e"], labels, ("relevant", "partial"))
    assert lenient["relative_recall"] == round(2 / 3, 3)
    assert gold.metrics([], {}, ("relevant",))["relative_recall"] is None


def test_question_fixture_is_well_formed():
    questions = json.loads((gold.GOLD / "questions.json").read_text(encoding="utf-8"))["questions"]
    assert len({q["id"] for q in questions}) == len(questions)
    for q in questions:
        assert q["blocks"] and q["criteria"]
        for block in q["blocks"]:
            assert block["role"] in {"any", "outcome", "condition", "exposure"}
            assert block["variants"] and block["seed"]


def test_merge_verifies_quotes_and_puts_doubtful_labels_first(tmp_path, monkeypatch):
    monkeypatch.setattr(gold, "GOLD", tmp_path)
    (tmp_path / "pool").mkdir()
    rows = [
        {"question_id": "q01", "study_id": "1", "found_by": "variants", "title": "Hepatic events in users"},
        {"question_id": "q01", "study_id": "2", "found_by": "seed", "title": "Asthma registry"},
    ]
    with (tmp_path / "pool" / "q01.csv").open("w", newline="", encoding="utf-8") as f:
        writer = gold.csv.DictWriter(f, gold.FIELDS)
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in gold.FIELDS} for r in rows)
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "q01": {
                    "1": {
                        "label": "relevant",
                        "confidence": "high",
                        "evidence_field": "title",
                        "evidence_quote": "Hepatic events",
                    },
                    "2": {
                        "label": "not_relevant",
                        "confidence": "high",
                        "evidence_field": "title",
                        "evidence_quote": "Asthma cohort",
                    },  # not in the title
                }
            }
        ),
        encoding="utf-8",
    )
    gold.merge([labels])
    merged = gold.read_csv(tmp_path / "pool" / "q01.csv")
    assert [r["study_id"] for r in merged] == ["2", "1"]  # the unverified quote is reviewed first
    assert merged[0]["confidence"] == "low" and merged[0]["reason"].startswith("[quote not found]")
    assert gold.labels_for("q01") == {"1": "relevant", "2": "not_relevant"}


def test_orderings_put_specific_in_role_matches_first():
    ranked = ["cat", "spec_other_field", "spec_role"]
    feats = {
        "cat": {"specific": 0, "in_role": 0, "type_fit": 1},
        "spec_other_field": {"specific": 1, "in_role": 0, "type_fit": 0},
        "spec_role": {"specific": 1, "in_role": 1, "type_fit": -1},
    }
    assert gold.order(ranked, feats, "base") == ranked
    assert gold.order(ranked, feats, "specificity") == ["spec_role", "spec_other_field", "cat"]


def test_ranking_metrics_reward_relevant_studies_near_the_top():
    labels = {"a": "relevant", "b": "partial", "c": "not_relevant"}
    perfect = gold.ranking_metrics(["a", "b", "c"], labels)
    assert perfect["ndcg@5"] == 1.0 and perfect["relevant@5"] == 1 and perfect["recall@20"] == 1.0
    reversed_ = gold.ranking_metrics(["c", "b", "a"], labels)
    assert reversed_["ndcg@5"] < perfect["ndcg@5"]
    assert gold.ranking_metrics([], {})["ndcg@5"] is None
