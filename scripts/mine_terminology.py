"""カタログ語彙から terminology.json の `related_terms` 候補を統計的に抽出し、採否判定を反映する。

各研究のタイトル・Outcomes・Medicinal condition・目的から 1〜3 語の n-gram を作り、概念の
`english_terms` に一致するアンカー研究とそれ以外の出現率を対数オッズ比で比べ、本番 FTS（NEAR）で
新規に一致する研究がある語を候補にする。採否は人手判定で、規則は
`docs/research/202609140758_terminology_mining.md` の「採否規則」、判定記録と由来
（`source` / `origin_round` / `sample_checks`）は `data/terminology_decisions.json` が正本。

使い方:
    uv run scripts/mine_terminology.py              # 候補を採掘して JSON 出力
    uv run scripts/mine_terminology.py --apply       # 採用済み判定を related_terms に反映
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import connect

from ema_rwe.storage import Repository, fts_match
from ema_rwe.vocabulary import canonical

REPO_ROOT = Path(__file__).resolve().parents[1]
TERMINOLOGY_PATH = REPO_ROOT / "data" / "terminology.json"
DB_PATH = REPO_ROOT / "data" / "ema.sqlite3"
CANDIDATES_PATH = REPO_ROOT / "data" / "terminology_candidates.json"
DECISIONS_PATH = REPO_ROOT / "data" / "terminology_decisions.json"

# `decisions.json` のトップレベルキーのうち concept_id ではないもの (第 5 巡で追加)。
# `apply_decisions` はこれらを概念として解釈せず読み飛ばす。
RESERVED_TOP_LEVEL_KEYS = frozenset({"reason_codes"})

# n-gram の長さ (語数)。1〜3 語をすべて候補にする。
MIN_NGRAM, MAX_NGRAM = 1, 3

# n-gram の先頭・末尾がこれらの語なら除外する (文の断片化を防ぐ)。
STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "and",
        "or",
        "with",
        "to",
        "by",
        "at",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "any",
        "all",
        "not",
        "no",
        "vs",
        "versus",
        "other",
        "others",
    ]
)

# 非 ASCII トークンの代わりに挿入する区切りプレースホルダ。実在の語と衝突しないダミー文字列。
_NON_ASCII_BREAK = "\x00non_ascii\x00"

# 概念のアンカー研究数がこれ未満なら、その概念は候補抽出をスキップする (統計量が不安定なため)。
MIN_ANCHORS_TO_MINE = 3
# 候補 n-gram の採否条件 (すべて満たすこと)。
MIN_CANDIDATE_A = 3
MIN_CANDIDATE_RATIO = 0.03
MIN_LOG_ODDS = math.log(8)
# 既収録判定: アンカー外シード (english_terms + related_terms) に一致しない研究での
# 新規一致研究数がこれ未満なら「既収録」として除外する。
MIN_NEW_MATCHES = 1
# 対数オッズ比のラプラス平滑化定数。
LAPLACE = 0.5
# 概念ごとに出力する上位候補数。
# 第 4 巡: 30 では NEAR 由来の共起断片が枠を埋め、規則上採用しうる語
# (`hba1c`, `cardiac failure`, `thromboembolic events` 等) が判定者に届かなかった
# (第 2 回検証 #6)。60 に拡大する。
TOP_K = 60

# `--apply` 時の安全弁。`src/ema_rwe/terminology.py` の Concept モデルの上限と揃える。
MAX_ACCEPT_PER_CONCEPT = 15
MAX_RELATED_TERMS = 40

# 略語規則: 正規化後の単一トークン (空白を含まない) の文字数によって扱いを変える。
# 3 文字以下は衝突リスクが高すぎるため採用禁止。4〜5 文字は研究タイトルを人手確認し、
# `decisions.json` の `abbreviation_checks` に確認結果 (verdict) が無ければ `--apply` を拒否する。
MAX_FORBIDDEN_ABBREVIATION_LEN = 3
MAX_CHECKED_ABBREVIATION_LEN = 5


@dataclass(frozen=True)
class StudyDoc:
    """1 研究ぶんの正規化済みテキストと n-gram 集合 (候補 n-gram の列挙にのみ使う)。"""

    study_id: str
    canonical_text: str
    ngrams: frozenset[tuple[str, ...]]


def build_ngrams(tokens: list[str]) -> frozenset[tuple[str, ...]]:
    """トークン列から 1〜3 語の n-gram 集合を作る。

    Why: 研究内の重複を無視し、研究単位の出現有無 (document frequency) を数えるため。
    What: 先頭・末尾がストップワードの n-gram、全トークンが数字のみの n-gram、
    非 ASCII トークンの区切りプレースホルダを含む n-gram、同一トークンを 2 回以上含む
    n-gram を除外する。最後の条件は第 4 巡で追加した (第 2 回検証 #3): 本番 FTS の
    NEAR(±3) 一致判定では "cardiac failure" と "failure cardiac failure" が同じ研究集合に
    一致するため、`dedup_subsumed()` が語順・語重複に鈍感な NEAR の性質を利用して清潔な
    bigram を語重複トリグラムに置き換えてしまう欠陥が実測された。トークンが重複する n-gram
    (例: `2 diabetes diabetes`, `failure cardiac failure`) は文の断片が重複結合しただけで
    独立した臨床用語ではないため、候補として一切生成しない。
    When: 2026/09/14 (JST)
    """
    grams: set[tuple[str, ...]] = set()
    total = len(tokens)
    for n in range(MIN_NGRAM, MAX_NGRAM + 1):
        for i in range(total - n + 1):
            gram = tuple(tokens[i : i + n])
            if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                continue
            if all(tok.isdigit() for tok in gram):
                continue
            if _NON_ASCII_BREAK in gram:
                continue
            if len(set(gram)) != len(gram):
                continue
            grams.add(gram)
    return frozenset(grams)


def collapse_permutation_grams(
    candidate_grams: set[tuple[str, ...]], gram_anchor_doc_counts: Counter[tuple[str, ...]]
) -> set[tuple[str, ...]]:
    """トークンの多重集合が同じ n-gram を、アンカー文書内で最も多く出現した1つの代表に集約する。

    Why: 本番 FTS の NEAR(±3) 一致は語順に鈍感なので、同じトークン集合の異なる並び順
    (例: `apixaban dabigatran rivaroxaban` の 4 通りの語順) は常に同じ研究集合に一致し、
    統計量 (`a`/`df_all`/`b_new`) も完全に等しくなる。束ねずに残すと、意味的に同一の候補が
    `TOP_K` の枠を複数消費するだけで判定者への情報量を増やさない (第 4 巡・第 2 回検証 #9。
    第 4 巡で導入したトークン重複除外の姉妹対策)。
    What: `candidate_grams` をトークンの多重集合 (`tuple(sorted(gram))`) でグルーピングし、
    各グループから 1 個だけ代表を残す。代表は `gram_anchor_doc_counts` (その語順がいくつの
    アンカー文書に出現したか) が最大のもの、同数なら語順を空白区切り文字列にしたときの
    辞書順で最初のものを選ぶ。
    When: 2026/09/14 (JST)
    """
    groups: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    for gram in candidate_grams:
        groups.setdefault(tuple(sorted(gram)), []).append(gram)
    representatives: set[tuple[str, ...]] = set()
    for members in groups.values():
        best = min(members, key=lambda g: (-gram_anchor_doc_counts.get(g, 0), " ".join(g)))
        representatives.add(best)
    return representatives


def load_studies(db_path: Path) -> list[StudyDoc]:
    """`studies.body` JSON からカタログ文書 (研究ごと) を読み取り専用で読み込む。

    Why: DB を書き換えずに (`mode=ro`)、候補 n-gram を列挙するための文書集合を再現可能に
    取り出すため。ここで作る文書集合は「どの英語表現を候補として試すか」の語彙源にすぎず、
    採否の統計量 (`a`/`df_all`/`b_new`) はこの文書集合ではなく本番 FTS 索引から計算する
    (第 3 巡、設計変更 10)。
    What: title + outcomes + conditions + objective を連結し、`canonical()` で正規化する。
    非 ASCII トークン (`related_terms` は ASCII 限定のため候補になり得ない) は削除せず区切り
    プレースホルダに置き換え、隣接語が誤って結合されて実在しない bigram/trigram が生成される
    のを防ぐ。
    When: 2026/09/14 (JST)
    """
    conn = connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, body FROM studies ORDER BY id").fetchall()
    finally:
        conn.close()
    docs = []
    for study_id, body in rows:
        record = json.loads(body)
        text = "\n".join(
            [
                record.get("title") or "",
                record.get("outcomes") or "",
                " ".join(record.get("conditions") or []),
                record.get("objective") or "",
            ]
        )
        canonical_text = canonical(text)
        tokens = [t if t.isascii() else _NON_ASCII_BREAK for t in canonical_text.split()]
        docs.append(StudyDoc(study_id, canonical_text, build_ngrams(tokens)))
    return docs


def log_odds_ratio(a: int, anchors: int, b: int, total: int) -> float:
    """ラプラス平滑化した対数オッズ比。

    Why: 小標本のアンカー集合でもゼロ割・過大評価を避けて安定した比較をするため。
    What: `a`=アンカー内出現研究数, `b`=非アンカー内出現研究数, `anchors`=アンカー総数,
    `total`=全研究数として log(((a+0.5)/(A-a+0.5)) / ((b+0.5)/((N-A)-b+0.5))) を返す。
    When: 2026/09/14 (JST)
    """
    others = total - anchors
    return math.log(((a + LAPLACE) / (anchors - a + LAPLACE)) / ((b + LAPLACE) / (others - b + LAPLACE)))


def dedup_subsumed(candidates: list[dict]) -> list[dict]:
    """同じ `a` を持ち、より長い候補に完全に包含される短い候補を落とす。

    Why: 「liver」「liver injury」のような部分集合が両方候補に残ると冗長なため。
    What: 候補の n-gram トークン列が、別候補の連続部分列であり、かつ `a` が等しい場合に短い方を除く。
    When: 2026/09/14 (JST)
    """
    kept = []
    for cand in candidates:
        gram = cand["gram"]
        subsumed = any(
            other is not cand
            and other["a"] == cand["a"]
            and len(other["gram"]) > len(gram)
            and _is_contiguous_subsequence(gram, other["gram"])
            for other in candidates
        )
        if not subsumed:
            kept.append(cand)
    return kept


def _is_contiguous_subsequence(short: tuple[str, ...], long_: tuple[str, ...]) -> bool:
    n = len(short)
    return any(long_[i : i + n] == short for i in range(len(long_) - n + 1))


def _term_words(term: str) -> list[str]:
    """語・句を本番検索と同じ方法で単語トークンに分割する。

    Why: `SARS-CoV-2` のようなハイフンを含む語は、production の
    `ema_rwe.terminology._content_words`/`search_phrases` が `\\w+` 抽出で別トークンに
    分割してから NEAR グループを組む。mining 側も同じ分割にしないと、本番検索が実際に
    見つける一致と統計量がずれる。
    What: `re.findall(r"\\w+", term)` で単語トークン列を返す。
    When: 2026/09/14 (JST)
    """
    return re.findall(r"\w+", term)


def _matched_study_ids_for_groups(db, groups: list[list[str]]) -> set[str]:
    """NEAR 語群のいずれかに一致する研究 ID の集合を、本番索引に対する 1 回の MATCH で得る。

    Why: 設計変更 10: 種語・候補 n-gram の一致判定を、本番検索が使う索引・演算子
    (`study_fts` の FTS5 `MATCH`、`fts_match()` が組み立てる NEAR/OR 式) にそのまま揃える。
    What: `fts_match(groups, None)` (role=any: 列を絞らない) でクエリ文字列を作り、
    `SELECT id FROM study_fts WHERE study_fts MATCH ?` を 1 回発行して一致 ID 集合を返す。
    `bm25` によるランキングは不要 (件数と集合だけが要る) なので使わない。
    When: 2026/09/14 (JST)
    """
    query = fts_match(groups, None)
    if not query:
        return set()
    rows = db.execute("SELECT id FROM study_fts WHERE study_fts MATCH ?", (query,)).fetchall()
    return {row[0] for row in rows}


def _matched_study_ids(db, terms: list[str]) -> set[str]:
    """`terms` のいずれか (語は共起必須、語句どうしは OR) に一致する研究 ID の和集合。

    Why: `seeds_for_anchor`/`seeds_for_coverage` は複数語句のリストである。設計変更 10 は
    その一致集合を「各語句を本番検索と同じ FTS で照合した和集合」と定義する。
    What: `terms` の各語句を `_term_words` でトークン化し、空でないものだけを NEAR グループ
    として `_matched_study_ids_for_groups` に渡す (グループ間は OR、fts_match の仕様どおり)。
    When: 2026/09/14 (JST)
    """
    groups = [g for g in (_term_words(t) for t in terms) if g]
    return _matched_study_ids_for_groups(db, groups)


def mine_concept(concept: dict, docs: list[StudyDoc], total: int, db) -> dict:
    """1 概念ぶんの候補 n-gram を、本番検索と同じ FTS 一致集合で採掘する。

    Why: 概念ごとに独立した閾値判定・重複整理をまとめて行うため。第 3 巡 (設計変更 10) で、
    アンカー集合・既収録判定・候補統計量 (`a`/`df_all`/`b_new`) の一致判定を、すべて
    `contains()` の部分文字列一致から `Repository`/`fts_match()` 経由の本番 FTS 一致に
    揃えた。これにより `b_new >= 1` は「本番検索で実際に候補を増やす語」を意味する。
    What: `english_terms` のみをアンカー種語とし (広義語を含む `related_terms` による
    アンカー希釈を避ける、第 2 巡の設計変更 1 を維持)、アンカー研究集合 `anchor_ids` を
    FTS 一致で定義する (`A = len(anchor_ids)`)。候補 n-gram はアンカー研究のカタログ本文
    (`docs`) から列挙し (列挙のみ、統計には使わない)、各候補について FTS 一致集合
    `ids` を取り、`a = len(ids & anchor_ids)`、`df_all = len(ids)`、
    `b_new = len(ids - covered_ids)` (`covered_ids` は `english_terms + related_terms`
    の FTS 一致集合) を計算する。
    When: 2026/09/14 (JST)
    """
    anchor_seeds = concept["english_terms"]
    coverage_seeds = concept["english_terms"] + concept.get("related_terms", [])

    anchor_ids = _matched_study_ids(db, anchor_seeds)
    anchors = len(anchor_ids)
    if anchors < MIN_ANCHORS_TO_MINE:
        return {
            "concept_id": concept["concept_id"],
            "anchor_count": anchors,
            "insufficient_anchors": True,
            "candidates": [],
        }

    covered_ids = _matched_study_ids(db, coverage_seeds)

    id_to_doc = {doc.study_id: doc for doc in docs}
    candidate_grams: set[tuple[str, ...]] = set()
    gram_anchor_doc_counts: Counter[tuple[str, ...]] = Counter()
    for study_id in anchor_ids:
        doc = id_to_doc.get(study_id)
        if doc is not None:
            candidate_grams |= doc.ngrams
            gram_anchor_doc_counts.update(doc.ngrams)
    # 第 5 巡: 語順違いの多重集合を1代表に畳み込む (第 4 巡・第 2 回検証 #9)。
    candidate_grams = collapse_permutation_grams(candidate_grams, gram_anchor_doc_counts)

    candidates = []
    for gram in candidate_grams:
        ids = _matched_study_ids_for_groups(db, [list(gram)])
        a = len(ids & anchor_ids)
        if a < MIN_CANDIDATE_A or (a / anchors) < MIN_CANDIDATE_RATIO:
            continue
        df_all = len(ids)
        b = df_all - a
        score = log_odds_ratio(a, anchors, b, total)
        if score < MIN_LOG_ODDS:
            continue
        b_new = len(ids - covered_ids)
        if b_new < MIN_NEW_MATCHES:
            continue
        candidates.append(
            {
                "gram": gram,
                "ngram": " ".join(gram),
                "a": a,
                "df_all": df_all,
                "log_odds": round(score, 3),
                "b_new": b_new,
            }
        )

    candidates = dedup_subsumed(candidates)
    candidates.sort(key=lambda c: (-c["log_odds"], -c["a"], c["ngram"]))
    top = [{k: c[k] for k in ("ngram", "a", "df_all", "log_odds", "b_new")} for c in candidates[:TOP_K]]
    return {
        "concept_id": concept["concept_id"],
        "anchor_count": anchors,
        "insufficient_anchors": False,
        "candidates": top,
    }


def mine_all(terminology_path: Path, db_path: Path) -> dict:
    """全概念ぶんの候補をまとめ、`data/terminology_candidates.json` の中身を組み立てる。

    Why: CLI のメイン処理から分離し、テストや再利用をしやすくするため。
    What: 概念一覧と研究文書 (候補列挙用) を読み込み、`Repository(db_path)` を読み取り専用
    的に開いて (スキーマが最新なら書き込まない) 1 本の接続を全概念で使い回し、概念ごとに
    `mine_concept` を呼んで結果を集約する。入力辞書の SHA256 を `params.input_terminology_sha256`
    に記録し (第 4 巡: 指示書の設計変更 7 どおり `params` の中に配置。第 3 巡は誤って兄弟キーに
    置いていた)、`--apply` 後に成果物がどの辞書状態から採掘されたか追跡できるようにする。
    When: 2026/09/14 (JST)
    """
    raw = terminology_path.read_bytes()
    concepts = json.loads(raw)
    docs = load_studies(db_path)
    total = len(docs)
    repo = Repository(db_path)
    with repo.connection() as db:
        concept_results = [mine_concept(c, docs, total, db) for c in concepts]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "params": {
            "ngram_range": [MIN_NGRAM, MAX_NGRAM],
            "min_anchors_to_mine": MIN_ANCHORS_TO_MINE,
            "min_candidate_a": MIN_CANDIDATE_A,
            "min_candidate_ratio": MIN_CANDIDATE_RATIO,
            "min_log_odds": round(MIN_LOG_ODDS, 6),
            "min_new_matches": MIN_NEW_MATCHES,
            "laplace": LAPLACE,
            "top_k": TOP_K,
            "matching": "production_fts (Repository.study_fts MATCH via fts_match(), role=any)",
            "input_terminology_sha256": hashlib.sha256(raw).hexdigest(),
        },
        "total_studies": total,
        "concepts": concept_results,
    }


def _classify_abbreviation(term: str) -> str | None:
    """略語規則に基づき、単一トークン語を forbidden/needs_check/None に分類する。

    Why: 3 文字以下の略語は他概念・他分野と衝突しやすく、4〜5 文字も要確認であるため、
    `apply_decisions` が機械的に判定できるようにする。
    What: 正規化後に空白を含む (複数語の) 語は対象外 (`None`)。単一トークンで
    `MAX_FORBIDDEN_ABBREVIATION_LEN` 文字以下なら `"forbidden"`、
    `MAX_CHECKED_ABBREVIATION_LEN` 文字以下なら `"needs_check"`、それより長ければ `None`。
    When: 2026/09/14 (JST)
    """
    key = canonical(term)
    if " " in key or not key:
        return None
    if len(key) <= MAX_FORBIDDEN_ABBREVIATION_LEN:
        return "forbidden"
    if len(key) <= MAX_CHECKED_ABBREVIATION_LEN:
        return "needs_check"
    return None


def _load_candidate_sets(candidates_path: Path) -> dict[str, set[str]]:
    """`terminology_candidates.json` から概念ごとの候補 n-gram (正規化済み) 集合を作る。

    Why: `apply_decisions` が「候補由来でない accept」を検出するための参照データ。
    What: 概念ごとの `candidates` リストの `ngram` を `canonical()` して集合にする。
    When: 2026/09/14 (JST)
    """
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    return {c["concept_id"]: {canonical(cand["ngram"]) for cand in c["candidates"]} for c in data["concepts"]}


def apply_decisions(
    terminology_path: Path,
    decisions_path: Path,
    candidates_path: Path = CANDIDATES_PATH,
) -> list[str]:
    """人手の採否 (`data/terminology_decisions.json`) を `related_terms` へ追記する。

    Why: 統計的候補の採否はスクリプトではなく人手判断であるため、`--apply` は
    `accept` に明記された語だけを反映する。候補由来でない手書き語の混入・短い略語の誤採用・
    他概念との境界侵犯を機械的に防ぐ。
    What: 概念ごとに `accept` リストを検査してから、既存 `english_terms`/`related_terms` と
    大小無視で重複しない範囲で `related_terms` 末尾に追記する。検査項目:
    (1) `accept` は `terminology_candidates.json` の候補 (正規化一致) に限る、
    (2) 正規化後 3 文字以下の単一トークン略語は禁止、
    (3) 4〜5 文字の単一トークン略語は `decisions.json` の `abbreviation_checks` に
    verdict="accept" の確認記録が無ければ拒否、
    (4) 採用語が他概念の `english_terms`/`related_terms` (正規化後) と一致する場合は拒否。
    トップレベルの予約キー `RESERVED_TOP_LEVEL_KEYS`（第 5 巡で追加した `reason_codes` の
    定型理由文マップ）は概念 ID ではないため読み飛ばす。
    When: 2026/09/14 (JST)

    Returns:
        更新した concept_id のリスト。
    """
    concepts = json.loads(terminology_path.read_text(encoding="utf-8"))
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    candidate_sets = _load_candidate_sets(candidates_path)
    by_id = {c["concept_id"]: c for c in concepts}

    # 概念境界チェック用: 正規化語 -> 現在それを保有する concept_id。
    term_owner: dict[str, str] = {}
    for c in concepts:
        for term in c.get("english_terms", []) + c.get("related_terms", []):
            term_owner.setdefault(canonical(term), c["concept_id"])

    updated = []
    for concept_id, decision in decisions.items():
        if concept_id in RESERVED_TOP_LEVEL_KEYS:
            continue
        accept = decision.get("accept", [])
        if len(accept) > MAX_ACCEPT_PER_CONCEPT:
            raise ValueError(f"{concept_id}: accept list exceeds {MAX_ACCEPT_PER_CONCEPT}")
        concept = by_id.get(concept_id)
        if concept is None:
            raise ValueError(f"Unknown concept_id in decisions.json: {concept_id}")
        candidate_set = candidate_sets.get(concept_id, set())
        abbreviation_checks = decision.get("abbreviation_checks", {})
        related = list(concept.get("related_terms", []))
        seen = {canonical(t) for t in concept.get("english_terms", []) + related}
        for term in accept:
            if not term.isascii() or len(term) > 150:
                raise ValueError(f"{concept_id}: related term must be ASCII and <=150 chars: {term!r}")
            key = canonical(term)
            if key in seen:
                continue
            if key not in candidate_set:
                raise ValueError(
                    f"{concept_id}: accept term {term!r} is not among the mined candidates "
                    f"(hand-added terms are prohibited)"
                )
            klass = _classify_abbreviation(term)
            if klass == "forbidden":
                raise ValueError(
                    f"{concept_id}: {term!r} is a single token of "
                    f"<= {MAX_FORBIDDEN_ABBREVIATION_LEN} normalized characters and cannot be accepted"
                )
            if klass == "needs_check":
                check = abbreviation_checks.get(term)
                if not check or check.get("verdict") != "accept":
                    raise ValueError(
                        f"{concept_id}: {term!r} is a {MAX_CHECKED_ABBREVIATION_LEN}-or-fewer-character "
                        f"abbreviation and requires an 'accept' verdict in decisions.json "
                        f"abbreviation_checks before it can be applied"
                    )
            owner = term_owner.get(key)
            if owner is not None and owner != concept_id:
                raise ValueError(
                    f"{concept_id}: accept term {term!r} (normalized {key!r}) already belongs to "
                    f"concept {owner!r}; concept boundaries must not overlap"
                )
            related.append(term)
            seen.add(key)
            term_owner[key] = concept_id
        if len(related) > MAX_RELATED_TERMS:
            raise ValueError(f"{concept_id}: related_terms would exceed {MAX_RELATED_TERMS}")
        if related != concept.get("related_terms", []):
            concept["related_terms"] = related
            updated.append(concept_id)
    terminology_path.write_text(json.dumps(concepts, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=f"{DECISIONS_PATH.name} の accept リストだけを related_terms へ反映して終了する。",
    )
    args = parser.parse_args()

    if args.apply:
        updated = apply_decisions(TERMINOLOGY_PATH, DECISIONS_PATH, CANDIDATES_PATH)
        print(f"Applied decisions to {len(updated)} concept(s): {', '.join(updated)}")
        print(f"Updated {TERMINOLOGY_PATH}")
        return

    result = mine_all(TERMINOLOGY_PATH, DB_PATH)
    CANDIDATES_PATH.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    mined = sum(1 for c in result["concepts"] if not c["insufficient_anchors"])
    print(
        f"Mined {mined}/{len(result['concepts'])} concepts ({result['total_studies']} studies) -> {CANDIDATES_PATH}"
    )


if __name__ == "__main__":
    main()
