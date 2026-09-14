"""scripts/mine_terminology.py の n-gram 抽出・スコアリング・FTS 一致判定の単体テスト（第 3 巡）。

Why: 第 3 巡 (設計変更 10) で、候補統計量 (`a`/`df_all`/`b_new`) の一致判定が `contains()`
部分文字列一致から本番検索と同じ FTS (`Repository`/`fts_match()`) に変わった。純粋な
Python 合成データ (`StudyDoc` を手組みした Counter) では、この経路をテストできない。
What: FTS を使う `mine_concept`/`_matched_study_ids*` は、`tmp_path` に小さな `Repository`
を作って `Study` を `upsert` し、実際の `study_fts` に対して検証する
(`tests/test_screening.py` の `study()` ヘルパーを流用)。DB を使わない
`build_ngrams`/`log_odds_ratio`/`dedup_subsumed`/`apply_decisions` は第 2 巡の合成データ
テストをそのまま維持する。
When: 2026/09/14 (JST)
"""

import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ema_rwe.domain import Study
from ema_rwe.ema import BASE
from ema_rwe.storage import Repository

_SPEC = importlib.util.spec_from_file_location(
    "mine_terminology", Path(__file__).resolve().parents[1] / "scripts" / "mine_terminology.py"
)
mt = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mt  # dataclasses needs the module registered to resolve annotations
_SPEC.loader.exec_module(mt)


def study(study_id, **fields):
    base = {
        "study_id": study_id,
        "title": f"Study {study_id}",
        "study_type": "Non-interventional study",
        "countries": ["France"],
        "source_url": f"{BASE}/study/{study_id}",
    }
    return Study(**{**base, **fields})


def _mine(tmp_path, concept, studies, name="mine.sqlite3"):
    """`studies` を tmp_path 上の新しい Repository に upsert し、実 FTS で `mine_concept` を呼ぶ。"""
    db_path = tmp_path / name
    repo = Repository(db_path)
    for s in studies:
        repo.upsert(s)
    docs = mt.load_studies(db_path)
    with repo.connection() as db:
        return mt.mine_concept(concept, docs, total=len(docs), db=db)


def test_build_ngrams_drops_boundary_stopwords_and_numeric_only():
    # Given: a short token stream with a leading stopword and a trailing all-numeric token
    tokens = ["of", "liver", "injury", "2019"]

    # When: building 1..3-grams
    grams = mt.build_ngrams(tokens)

    # Then: n-grams starting/ending on a stopword are excluded...
    assert ("of", "liver") not in grams
    # ...a purely-numeric n-gram is excluded...
    assert ("2019",) not in grams
    # ...but a mixed n-gram merely ending in a number is kept (only a stopword boundary,
    # or an all-numeric gram, is disqualifying)
    assert ("injury", "2019") in grams
    assert ("liver", "injury") in grams
    assert ("liver",) in grams and ("injury",) in grams


def test_build_ngrams_respects_max_length():
    tokens = ["cat", "dog", "fox", "owl"]
    grams = mt.build_ngrams(tokens)
    assert max(len(g) for g in grams) == mt.MAX_NGRAM
    assert ("cat", "dog", "fox") in grams
    assert ("cat", "dog", "fox", "owl") not in grams


def test_build_ngrams_drops_grams_containing_the_non_ascii_break_marker():
    # Given: a token stream where a non-ASCII word has been replaced by the break marker
    # (load_studies() does this instead of deleting the word, per design change 9)
    tokens = ["tki", mt._NON_ASCII_BREAK, "advanced"]

    # When: building n-grams
    grams = mt.build_ngrams(tokens)

    # Then: no gram spans across the marker (no fabricated "tki advanced" bigram)...
    assert ("tki", "advanced") not in grams
    # ...but the single-word grams on either side still exist
    assert ("tki",) in grams
    assert ("advanced",) in grams


def test_build_ngrams_drops_grams_with_a_repeated_token():
    # Given: a token stream that (like a study's title and outcomes text concatenated across
    # a shared phrase boundary) contains "cardiac failure" twice back-to-back, producing a
    # literal repeated-token 3-gram alongside the clean 2-gram (round 4, second verification
    # report #3: NEAR(+-3) FTS matches "cardiac failure" and "failure cardiac failure" to the
    # same study set, so dedup_subsumed() previously kept the noisy trigram over the clean
    # bigram because both shared the same `a`).
    tokens = ["risk", "cardiac", "failure", "cardiac", "failure", "hospitalization"]

    grams = mt.build_ngrams(tokens)

    # Then: the clean bigram is still generated...
    assert ("cardiac", "failure") in grams
    # ...but any n-gram containing the same token twice is excluded outright, so it can never
    # reach dedup_subsumed() in the first place.
    assert ("cardiac", "failure", "cardiac") not in grams
    assert ("failure", "cardiac", "failure") not in grams


def test_collapse_permutation_grams_prefers_highest_anchor_count_then_alphabetical():
    # Given: two multisets of tokens. The first has three order-permutations that all matched
    # the same NEAR query and therefore look like independent candidates today (round 5,
    # final verification report #9: "apixaban dabigatran rivaroxaban" style drug-list
    # permutations waste TOP_K slots on statistically-identical duplicates); one permutation
    # occurs in more anchor documents than the others. The second multiset has two
    # permutations tied on anchor-document count, which must fall back to alphabetical order.
    # An unrelated bigram (no permutation twin) must survive untouched.
    candidate_grams = {
        ("apixaban", "dabigatran", "rivaroxaban"),
        ("dabigatran", "rivaroxaban", "apixaban"),
        ("rivaroxaban", "apixaban", "dabigatran"),
        ("zeta", "alpha"),
        ("alpha", "zeta"),
        ("liver", "injury"),
    }
    gram_anchor_doc_counts = Counter(
        {
            ("apixaban", "dabigatran", "rivaroxaban"): 5,
            ("dabigatran", "rivaroxaban", "apixaban"): 3,
            ("rivaroxaban", "apixaban", "dabigatran"): 3,
            ("zeta", "alpha"): 2,
            ("alpha", "zeta"): 2,
            ("liver", "injury"): 4,
        }
    )

    # When: collapsing permutation duplicates
    kept = mt.collapse_permutation_grams(candidate_grams, gram_anchor_doc_counts)

    # Then: the drug-triple multiset keeps only its highest-anchor-count order, the tied
    # multiset falls back to alphabetical order, and the lone bigram is untouched.
    assert kept == {
        ("apixaban", "dabigatran", "rivaroxaban"),
        ("alpha", "zeta"),
        ("liver", "injury"),
    }


def test_log_odds_ratio_is_positive_when_anchor_enriched():
    # Given: a term seen in 8/10 anchors but only 2/90 non-anchors
    score = mt.log_odds_ratio(a=8, anchors=10, b=2, total=100)
    assert score > 0


def test_log_odds_ratio_is_zero_when_prevalence_matches():
    # Given: identical prevalence inside and outside the anchor set
    score = mt.log_odds_ratio(a=5, anchors=10, b=45, total=100)
    assert math.isclose(score, 0.0, abs_tol=1e-9)


def test_log_odds_ratio_laplace_smoothing_avoids_division_by_zero():
    # Given: a term present in every anchor and absent everywhere else (a == anchors, b == 0)
    score = mt.log_odds_ratio(a=10, anchors=10, b=0, total=100)
    assert math.isfinite(score) and score > 0


def test_dedup_subsumed_drops_shorter_equal_count_subsequence():
    candidates = [
        {"gram": ("liver",), "ngram": "liver", "a": 10, "df_all": 20, "log_odds": 1.0},
        {"gram": ("liver", "injury"), "ngram": "liver injury", "a": 10, "df_all": 12, "log_odds": 2.0},
        {"gram": ("acute", "kidney"), "ngram": "acute kidney", "a": 5, "df_all": 9, "log_odds": 1.5},
    ]

    kept = mt.dedup_subsumed(candidates)

    kept_ngrams = {c["ngram"] for c in kept}
    assert "liver" not in kept_ngrams  # subsumed by "liver injury" with the same a
    assert "liver injury" in kept_ngrams
    assert "acute kidney" in kept_ngrams  # not subsumed by anything


def test_dedup_subsumed_keeps_shorter_gram_when_counts_differ():
    candidates = [
        {"gram": ("liver",), "ngram": "liver", "a": 15, "df_all": 30, "log_odds": 1.0},
        {"gram": ("liver", "injury"), "ngram": "liver injury", "a": 10, "df_all": 12, "log_odds": 2.0},
    ]

    kept = mt.dedup_subsumed(candidates)

    # a differs (15 vs 10), so the shorter gram is not considered a redundant subset
    assert {c["ngram"] for c in kept} == {"liver", "liver injury"}


def test_term_words_splits_hyphenated_terms_like_production_search():
    # Design change 10: mining must tokenize seeds the same way ema_rwe.terminology
    # (search_phrases/_content_words) does, so a hyphenated term becomes two NEAR-grouped
    # words rather than one glued token.
    assert mt._term_words("SARS-CoV-2") == ["SARS", "CoV", "2"]
    assert mt._term_words("toy disease") == ["toy", "disease"]


def test_matched_study_ids_for_groups_uses_near_cooccurrence(tmp_path):
    # Given: one study whose title has the two words adjacent, and one where the words are
    # separated by more than the NEAR(..., 3) window (4+ words apart), so it must not match
    # (mirrors test_screening.py's test_multi_word_query_does_not_match_split_words).
    db_path = tmp_path / "match.sqlite3"
    repo = Repository(db_path)
    repo.upsert(study("1", title="Hepatic failure after chemotherapy"))
    repo.upsert(study("2", title="Heart failure admissions among patients with hepatic comorbidity"))
    repo.upsert(study("3", title="Completely unrelated study"))

    with repo.connection() as db:
        ids = mt._matched_study_ids_for_groups(db, [["hepatic", "failure"]])

    # Then: only the study where "hepatic" and "failure" co-occur within 3 tokens matches
    assert ids == {"1"}


def test_mine_concept_flags_insufficient_anchors_below_threshold(tmp_path):
    concept = {"concept_id": "toy_concept", "english_terms": ["toy disease"], "related_terms": []}
    studies = [
        study("1", conditions=["Toy disease"]),
        study("2", conditions=["Unrelated condition"]),
    ]

    result = _mine(tmp_path, concept, studies)

    assert result["anchor_count"] == 1
    assert result["insufficient_anchors"] is True
    assert result["candidates"] == []


def test_mine_concept_surfaces_anchor_enriched_ngram_with_new_coverage(tmp_path):
    # Given: 6 anchor studies (matched via "toy disease" in conditions) whose outcomes all
    # report "biomarker x elevated"; one further, non-anchor study also reports it (giving
    # b_new >= 1); one more non-anchor study reports nothing relevant at all.
    concept = {"concept_id": "toy_disease", "english_terms": ["toy disease"], "related_terms": []}
    anchors = [
        study(str(i), conditions=["Toy disease"], outcomes="Biomarker x elevated levels observed")
        for i in range(1, 7)
    ]
    others = [
        study(
            "7",
            conditions=["Unrelated condition"],
            outcomes="This document reports biomarker x elevated readings in an unrelated context",
        ),
        study("8", conditions=["Something else entirely"], outcomes="No relevant marker mentioned at all"),
    ]

    result = _mine(tmp_path, concept, anchors + others)

    assert result["insufficient_anchors"] is False
    ngrams = {c["ngram"] for c in result["candidates"]}
    assert "biomarker x elevated" in ngrams
    assert "toy disease" not in ngrams  # the seed itself is not re-suggested (b_new == 0)
    assert "toy" not in ngrams and "disease" not in ngrams  # seed sub-tokens: also b_new == 0


def test_mine_concept_excludes_ngram_with_zero_new_matches(tmp_path):
    # Given: a candidate phrase ("flare episode") that only ever appears inside studies
    # already covered by the concept's own anchor term ("toy disease") -> b_new == 0.
    concept = {"concept_id": "toy_disease", "english_terms": ["toy disease"], "related_terms": []}
    anchors = [
        study(str(i), conditions=["Toy disease"], outcomes="Toy disease flare episode noted repeatedly")
        for i in range(1, 7)
    ]
    others = [study(str(i), conditions=["Unrelated"], outcomes="Nothing relevant here") for i in range(7, 37)]

    result = _mine(tmp_path, concept, anchors + others)

    ngrams = {c["ngram"] for c in result["candidates"]}
    # "flare episode" only occurs inside already-covered anchor documents (b_new == 0),
    # so it must be excluded even though a/A and log-odds would otherwise pass comfortably.
    assert "flare episode" not in ngrams


def test_mine_concept_new_matches_boundary_b_new_zero_vs_one(tmp_path):
    # Given: MIN_NEW_MATCHES = 1. A candidate phrase ("flareup noted") that only occurs
    # inside the 10 anchor studies (already covered) has b_new == 0 and must be excluded.
    concept = {"concept_id": "toy", "english_terms": ["toy disease"], "related_terms": []}
    anchors = [
        study(str(i), conditions=["Toy disease"], outcomes="Toy disease flareup noted repeatedly")
        for i in range(1, 11)
    ]
    others = [
        study(str(i), conditions=["Unrelated"], outcomes="Nothing relevant here") for i in range(11, 41)
    ]

    result_zero = _mine(tmp_path, concept, anchors + others, name="zero.sqlite3")
    assert "flareup noted" not in {c["ngram"] for c in result_zero["candidates"]}

    # When: one further, non-anchor, non-covered study also mentions the same phrase
    # -> b_new becomes 1 -> the candidate must now be surfaced.
    extra = study("99", conditions=["A different unrelated context"], outcomes="flareup noted here too")
    result_one = _mine(tmp_path, concept, anchors + others + [extra], name="one.sqlite3")
    assert "flareup noted" in {c["ngram"] for c in result_one["candidates"]}


def test_mine_concept_min_candidate_a_boundary(tmp_path):
    # Given: MIN_CANDIDATE_A = 3. With 10 anchors and a large (40-study) non-anchor pool of
    # which exactly one mentions "passcand", a/A and log-odds are comfortably clear at a=3
    # (isolating the `a` check): 3 of the 10 anchors mention "passcand" (passes) vs 2 (fails,
    # purely because a < MIN_CANDIDATE_A -- the a/A ratio at a=2 is still 0.2, far above the
    # 0.03 minimum).
    concept = {"concept_id": "toy", "english_terms": ["toy disease"], "related_terms": []}

    def build(a_occurrences: int):
        anchors = [
            study(
                str(i + 1),
                conditions=["Toy disease"],
                outcomes="passcand marker noted" if i < a_occurrences else "no marker noted",
            )
            for i in range(10)
        ]
        others = [
            study(
                str(100000 + i),
                conditions=["Unrelated"],
                outcomes="an unrelated document that also contains passcand marker"
                if i == 0
                else "unrelated only",
            )
            for i in range(40)
        ]
        return anchors + others

    result_pass = _mine(tmp_path, concept, build(mt.MIN_CANDIDATE_A), name="pass.sqlite3")
    assert "passcand marker" in {c["ngram"] for c in result_pass["candidates"]}

    result_fail = _mine(tmp_path, concept, build(mt.MIN_CANDIDATE_A - 1), name="fail.sqlite3")
    assert "passcand marker" not in {c["ngram"] for c in result_fail["candidates"]}


def test_mine_concept_min_candidate_ratio_boundary(tmp_path):
    # Given: a fixed at MIN_CANDIDATE_A occurrences. Derive (round 4, second verification
    # report #12) the passing anchor count directly from MIN_CANDIDATE_RATIO rather than
    # hardcoding it, so the test keeps exercising the exact boundary if the threshold changes:
    # a/A == MIN_CANDIDATE_RATIO exactly at A = a / MIN_CANDIDATE_RATIO (passes, >=); one more
    # anchor pushes a/A just below the threshold (fails). A large non-anchor pool with exactly
    # one "passcand" mention keeps log-odds comfortably clear in the passing case, so the ratio
    # check is what's actually exercised.
    concept = {"concept_id": "toy", "english_terms": ["toy disease"], "related_terms": []}
    a_fixed = mt.MIN_CANDIDATE_A
    anchors_pass = round(a_fixed / mt.MIN_CANDIDATE_RATIO)
    assert a_fixed / anchors_pass >= mt.MIN_CANDIDATE_RATIO  # sanity: derivation lands on >=
    assert a_fixed / (anchors_pass + 1) < mt.MIN_CANDIDATE_RATIO

    def build(n_anchor: int):
        anchors = [
            study(
                str(i + 1),
                conditions=["Toy disease"],
                outcomes="passcand marker noted" if i < a_fixed else "no marker noted",
            )
            for i in range(n_anchor)
        ]
        others = [
            study(
                str(100000 + i),
                conditions=["Unrelated"],
                outcomes="an unrelated document that also contains passcand marker"
                if i == 0
                else "unrelated only",
            )
            for i in range(400)
        ]
        return anchors + others

    result_pass = _mine(tmp_path, concept, build(anchors_pass), name="pass.sqlite3")
    assert "passcand marker" in {c["ngram"] for c in result_pass["candidates"]}

    result_fail = _mine(tmp_path, concept, build(anchors_pass + 1), name="fail.sqlite3")
    assert "passcand marker" not in {c["ngram"] for c in result_fail["candidates"]}


def test_mine_concept_min_log_odds_boundary(tmp_path):
    # Given: MIN_LOG_ODDS = log(8). Fix anchors=20 with every anchor mentioning "passcand"
    # (a=20, a/A=1.0, well above the count/ratio thresholds), and 180 non-anchor documents of
    # which `b` contain "passcand" too (none of them covered, so b_new == b whenever b >= 1).
    # Solve (via the real log_odds_ratio function, not a hardcoded number) for the largest b
    # whose score still clears log(8), and confirm mine_concept includes "passcand marker"
    # there but excludes it once b grows by one more occurrence.
    anchors_n, others_n, a = 20, 180, 20
    target = math.log(8)

    def score_for_b(b):
        return mt.log_odds_ratio(a, anchors_n, b, anchors_n + others_n)

    b_pass = 0
    while score_for_b(b_pass + 1) >= target:
        b_pass += 1
    assert score_for_b(b_pass) >= target
    b_fail = b_pass + 1
    assert score_for_b(b_fail) < target

    concept = {"concept_id": "toy", "english_terms": ["toy disease"], "related_terms": []}

    def build(b_occurrences: int):
        anchors = [
            study(str(i + 1), conditions=["Toy disease"], outcomes="passcand marker noted")
            for i in range(anchors_n)
        ]
        others = [
            study(
                str(100000 + i),
                conditions=["Unrelated"],
                outcomes=(
                    "unrelated control text with passcand marker noise"
                    if i < b_occurrences
                    else "unrelated only"
                ),
            )
            for i in range(others_n)
        ]
        return anchors + others

    result_pass = _mine(tmp_path, concept, build(b_pass), name="pass.sqlite3")
    assert "passcand marker" in {c["ngram"] for c in result_pass["candidates"]}

    result_fail = _mine(tmp_path, concept, build(b_fail), name="fail.sqlite3")
    assert "passcand marker" not in {c["ngram"] for c in result_fail["candidates"]}


def _write_toy_terminology(path: Path, related_terms=None):
    path.write_text(
        json.dumps(
            [
                {
                    "concept_id": "toy_concept",
                    "input_terms": ["toy"],
                    "english_terms": ["toy disease"],
                    "related_terms": related_terms or ["existing term"],
                    "code_candidates": [],
                },
                {
                    "concept_id": "other_concept",
                    "input_terms": ["other"],
                    "english_terms": ["other disease"],
                    "related_terms": ["other marker"],
                    "code_candidates": [],
                },
            ]
        ),
        encoding="utf-8",
    )


def _write_toy_candidates(path: Path, ngrams: list[str]):
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-14T00:00:00Z",
                "input_terminology_sha256": "deadbeef",
                "params": {},
                "total_studies": 10,
                "concepts": [
                    {
                        "concept_id": "toy_concept",
                        "anchor_count": 10,
                        "insufficient_anchors": False,
                        "candidates": [
                            {"ngram": n, "a": 5, "df_all": 6, "log_odds": 3.0, "b_new": 2} for n in ngrams
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_apply_decisions_appends_only_accepted_terms_and_dedupes(tmp_path):
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["new term", "existing term"])
    decisions_path.write_text(
        json.dumps(
            {
                "toy_concept": {
                    "accept": ["new term", "existing term"],  # duplicate should be skipped
                    "reject": {"noise": "not relevant"},
                }
            }
        ),
        encoding="utf-8",
    )

    updated = mt.apply_decisions(terminology_path, decisions_path, candidates_path)

    concepts = json.loads(terminology_path.read_text(encoding="utf-8"))
    assert updated == ["toy_concept"]
    toy = next(c for c in concepts if c["concept_id"] == "toy_concept")
    assert toy["related_terms"] == ["existing term", "new term"]


def test_apply_decisions_skips_reserved_top_level_keys(tmp_path):
    # Given: a decisions.json with the round-5 `reason_codes` compression map (a top-level
    # sibling of the concept_id entries, not itself a concept) alongside a real decision.
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["new term"])
    decisions_path.write_text(
        json.dumps(
            {
                "reason_codes": {"r1": "some boilerplate reject reason"},
                "toy_concept": {"accept": ["new term"], "reject": {}},
            }
        ),
        encoding="utf-8",
    )

    # When/Then: apply_decisions must not treat "reason_codes" as an unknown concept_id...
    updated = mt.apply_decisions(terminology_path, decisions_path, candidates_path)
    # ...and must still apply the real concept's decision.
    assert updated == ["toy_concept"]


def test_apply_decisions_rejects_accept_list_over_limit(tmp_path):
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    terms = [f"term{i}" for i in range(mt.MAX_ACCEPT_PER_CONCEPT + 1)]
    _write_toy_candidates(candidates_path, terms)
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": terms}}),
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_apply_decisions_rejects_hand_added_term_not_in_candidates(tmp_path):
    # Design change 6: accept must be drawn from terminology_candidates.json (normalized match).
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["mined term"])
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": ["hand written term"]}}),
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError as exc:
        raised = "not among the mined candidates" in str(exc)
    assert raised


def test_apply_decisions_rejects_forbidden_short_abbreviation(tmp_path):
    # Design change 4: normalized single tokens of <= 3 characters cannot be accepted.
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["mi"])
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": ["mi"]}}),
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError as exc:
        raised = "cannot be accepted" in str(exc)
    assert raised


def test_apply_decisions_requires_abbreviation_check_for_4_to_5_char_token(tmp_path):
    # Design change 4: 4-5 character single-token abbreviations need a verified 'accept'
    # verdict in decisions.json's abbreviation_checks before --apply will take them.
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["abcd"])
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": ["abcd"]}}),  # no abbreviation_checks entry
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError as exc:
        raised = "abbreviation_checks" in str(exc)
    assert raised

    # With a verified 'accept' verdict present, the same term is applied successfully.
    decisions_path.write_text(
        json.dumps(
            {
                "toy_concept": {
                    "accept": ["abcd"],
                    "abbreviation_checks": {"abcd": {"verdict": "accept", "studies": []}},
                }
            }
        ),
        encoding="utf-8",
    )
    updated = mt.apply_decisions(terminology_path, decisions_path, candidates_path)
    assert updated == ["toy_concept"]


def test_apply_decisions_rejects_concept_boundary_overlap(tmp_path):
    # Design change 5: an accepted term must not normalize-match another concept's
    # english_terms/related_terms.
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["other marker"])  # already owned by other_concept
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": ["other marker"]}}),
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError as exc:
        raised = "concept boundaries must not overlap" in str(exc)
    assert raised


def test_apply_decisions_rejects_related_terms_over_max(tmp_path):
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    existing = [f"existing{i}" for i in range(mt.MAX_RELATED_TERMS - 1)]
    _write_toy_terminology(terminology_path, related_terms=existing)
    _write_toy_candidates(candidates_path, ["term a", "term b"])
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": ["term a", "term b"]}}),
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError as exc:
        raised = "would exceed" in str(exc)
    assert raised


def test_apply_decisions_rejects_unknown_concept_id(tmp_path):
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["term a"])
    decisions_path.write_text(
        json.dumps({"nonexistent_concept": {"accept": ["term a"]}}),
        encoding="utf-8",
    )

    try:
        mt.apply_decisions(terminology_path, decisions_path, candidates_path)
        raised = False
    except ValueError as exc:
        raised = "Unknown concept_id" in str(exc)
    assert raised


def test_mine_all_records_input_sha256_inside_params(tmp_path):
    # Design change 7 (round 2) requires input_terminology_sha256 to be recorded so an
    # artefact can be traced back to the dictionary state it was mined from. Round 3 placed it
    # as a sibling of "params" instead of inside it; round 4 (second verification report #10)
    # fixes the placement.
    import hashlib

    terminology_path = tmp_path / "terminology.json"
    _write_toy_terminology(terminology_path)
    db_path = tmp_path / "mine_all.sqlite3"
    repo = Repository(db_path)
    repo.upsert(study("1", conditions=["Toy disease"]))

    result = mt.mine_all(terminology_path, db_path)

    assert (
        result["params"]["input_terminology_sha256"]
        == hashlib.sha256(terminology_path.read_bytes()).hexdigest()
    )
    assert "input_terminology_sha256" not in result


def test_apply_decisions_preserves_immutable_fields_and_concept_count(tmp_path):
    # Regression guard: --apply must never touch english_terms/code_candidates/input_terms/
    # concept_id, and must never add or remove concepts.
    terminology_path = tmp_path / "terminology.json"
    decisions_path = tmp_path / "decisions.json"
    candidates_path = tmp_path / "candidates.json"
    _write_toy_terminology(terminology_path)
    _write_toy_candidates(candidates_path, ["new term"])
    decisions_path.write_text(
        json.dumps({"toy_concept": {"accept": ["new term"]}}),
        encoding="utf-8",
    )
    before = json.loads(terminology_path.read_text(encoding="utf-8"))

    mt.apply_decisions(terminology_path, decisions_path, candidates_path)

    after = json.loads(terminology_path.read_text(encoding="utf-8"))
    assert len(after) == len(before)
    assert [c["concept_id"] for c in after] == [c["concept_id"] for c in before]
    for b, a in zip(before, after, strict=True):
        assert a["concept_id"] == b["concept_id"]
        assert a["input_terms"] == b["input_terms"]
        assert a["english_terms"] == b["english_terms"]
        assert a["code_candidates"] == b["code_candidates"]
