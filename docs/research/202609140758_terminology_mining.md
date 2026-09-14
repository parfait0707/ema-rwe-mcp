# カタログ語彙からの関連語導出（全概念）— 最終状態（第 5 巡）

## 目的

`data/terminology.json` の 48 概念すべてについて、カタログ自身の語彙（研究の
title/outcomes/conditions/objective）から `related_terms` 候補を統計的に抽出し、
機械検査（候補由来・略語・概念境界・上限）と人手判定を経て `related_terms` に反映する。
本ドキュメントは 5 巡にわたる改訂の**最終状態のみ**を記す。各巡の経緯は末尾の
「改訂履歴」に要約する。

## 前提と制約

- DB は読み取り専用（`sqlite3.connect("file:...?mode=ro")` / `Repository` の読み取り専用的な
  オープン）。`data/ema.sqlite3` は本作業を通じて不変（SHA256 で確認済み）。
- 変更対象は `related_terms` のみ。`english_terms` / `code_candidates` / `input_terms` /
  `concept_id` は不変。概念の追加・削除もしていない。
- mining の入力は、本ドキュメントが記す採否判定をまだ反映していない辞書
  （`git show <本ドキュメントの内容を採用したコミットの直前のコミット>:data/terminology.json`、
  SHA256 `261e9ee6f125ecaf31b2377c30133155f9c9fd2f70046e02fbdfbe13d545b8fc`。
  第 1〜5 巡を通じて `data/terminology.json` はまだ一度もコミットされていないため、
  この値は本リポジトリの現在の HEAD と一致する）。作業時は
  `git checkout -- data/terminology.json` で辞書をこの状態に戻してから
  mining → 判定 → `--apply` の順に行った。**注意（第 5 巡で訂正、最終検証報告 3 節）**:
  本ドキュメントの採否を一度コミットすると、以後の HEAD は**適用後**の辞書になる。
  適用後の辞書を入力に再採掘すると、採用語はすべて `coverage_seeds`
  （`english_terms + related_terms`）に含まれるため `b_new = 0` となり候補から消える
  （`--apply` はエラーにならず、既収録語を `continue` でスキップして静かに何もしない）。
  次回この手順を再実行するときは、**本ドキュメントの採否がまだ適用されていないコミット**
  （＝本ドキュメントを含むコミットの 1 つ前）の `data/terminology.json` を入力にすること。
- 一致判定（アンカー・既収録・候補統計量）はすべて本番検索と同じ FTS
  （`Repository`/`storage.fts_match()` 経由の `study_fts MATCH`、`role=any`）で行う。
  `contains()` による部分文字列一致は使わない。
- `data/terminology_decisions.json` は**第 4 巡からコミット対象**（`.gitignore` に
  `!data/terminology_decisions.json` を追加）。各判定に `source`
  （`carried_over`/`new`/`supervisor`）と引き継ぎ元の巡（`origin_round`）を持たせ、
  この JSON 自体が判定根拠の正本になるようにした。`data/terminology_candidates.json` は
  `uv run scripts/mine_terminology.py` で再生成できるため引き続き追跡しない。
  **第 5 巡**: 不採用理由の 83% が少数の定型文の重複だった（1.63 MB 中 1.42 MB）ため、
  トップレベルに `reason_codes`（コード→定型理由文のマップ）を追加し、各 `reject` エントリは
  個別理由が必要なものを除いて `reason_code` で参照する形に圧縮した
  （1.63 MB → 0.33 MB、-80%）。スキーマは後述「`decisions.json` の判定スキーマ」節を参照。

## 手順

1. **辞書リセット**: `git checkout -- data/terminology.json` で、本ドキュメントの採否が
   まだ反映されていない辞書（上記「前提と制約」参照）に戻す。
2. **文書構築**（`load_studies()`）: 各研究について
   `title + "\n" + outcomes + "\n" + " ".join(conditions) + "\n" + objective` を連結し
   `canonical()` で正規化。非 ASCII トークンは削除せず区切りプレースホルダに置換してから
   トークン化する（実在しない bigram の生成を防ぐ）。
3. **アンカー研究**（`mine_concept()`）: 概念ごとに `anchor_seeds = english_terms`
   （`related_terms` は含めない。広義語によるアンカー希釈を避ける）。各語句を
   `_term_words()` でトークン化し、`fts_match([words], None)`（列を絞らない `role=any`）で
   `study_fts` に `MATCH` を発行して一致研究 ID の和集合を取り、アンカー数 `A` とする。
4. **n-gram 候補**（`build_ngrams()`）: アンカー研究の文書から 1〜3 語の n-gram を抽出する。
   先頭・末尾ストップワード、全数字のみ、非 ASCII 区切りを含む n-gram に加えて、
   **同一トークンを 2 回以上含む n-gram を除外する**（第 4 巡で追加。理由は次節）。
   続けて、アンカー研究全体で集めた候補 n-gram 集合を
   **トークンの多重集合ごとに 1 代表へ畳み込む**（`collapse_permutation_grams()`、
   第 5 巡で追加。理由は次節）。代表はアンカー文書内の出現研究数が最大のもの、
   同数なら語順を空白区切り文字列にしたときの辞書順で最初のもの。
5. **スコア**: `a` = アンカー内で当該候補に本番 FTS が一致する研究数、
   `df_all` = 全研究中で一致する研究数、`b = df_all - a`、ラプラス平滑化した対数オッズ比
   `log(((a+0.5)/(A-a+0.5)) / ((b+0.5)/((N-A)-b+0.5)))`。
6. **既収録判定**: `coverage_seeds = english_terms + related_terms`（現状値）の FTS 一致集合
   に含まれない研究での候補の一致研究数 `b_new` を数える。`b_new == 0` は既収録として除外。
7. **候補条件（すべて満たす）**: `a>=3`、`a/A>=0.03`、対数オッズ比 `>=log(8)`、`b_new>=1`。
   重複整理（`dedup_subsumed()`: 同じ `a` を持ち、より長い候補に完全に包含される短い候補を
   落とす）の後、対数オッズ降順で上位 `TOP_K=60` 件を候補として出力する。
8. **採否判定（人手）**: 下記「採否規則」に従って候補を採用/不採用に判定し、
   `data/terminology_decisions.json` に `{concept_id: {accept, reject, accept_provenance,
   abbreviation_checks?, sample_checks?}}` として記録する（スキーマは後述）。
9. **反映**: `uv run scripts/mine_terminology.py --apply` が、候補由来チェック・略語規則・
   概念境界チェック・上限チェックをすべて通過した `accept` だけを `related_terms` へ追記する。

## パラメータ（最終値）

| パラメータ | 値 | 意味 |
|---|---|---|
| n-gram 長 | 1〜3 語 | 抽出する n-gram の語数範囲 |
| `min_anchors_to_mine` | 3 | 概念のアンカー数がこれ未満なら候補抽出をスキップ |
| `min_candidate_a` | 3 | 候補 n-gram のアンカー内出現研究数の下限 |
| `min_candidate_ratio` | 0.03 | 候補 n-gram のアンカー内出現率の下限 (a/A) |
| `min_log_odds` | 2.0794 = log 8 | 対数オッズ比の下限 |
| `min_new_matches` | 1 | 既存カバレッジ種語に一致しない研究での新規一致件数 `b_new` の下限 |
| `laplace` | 0.5 | 対数オッズ比の平滑化定数 |
| `top_k` | **30 → 60**（第 4 巡） | 概念ごとに出力する上位候補数 |
| トークン重複除外 | **(第 4 巡で新設)** | 同一トークンを 2 回以上含む n-gram は候補生成の時点で除外 |
| 順列重複の畳み込み | **(第 5 巡で新設)** | トークンの多重集合が同じ n-gram (`apixaban dabigatran rivaroxaban` の語順違い等) は 1 代表 (アンカー文書内出現数最大、同数は辞書順) に畳み込む |
| アンカー種語 | `english_terms` のみ | アンカー研究の定義に使う種語集合 |
| 既収録判定 | `b_new == 0` | 既存語で充足済みとみなす基準 |
| 一致方式 | 本番 FTS（`Repository.study_fts MATCH` via `fts_match()`, `role=any`） | `contains()` 部分文字列一致は不使用 |
| 略語規則 | 3 文字以下禁止・4〜5 文字要確認 | `--apply` 時の機械検査 |
| 概念境界検査 | 正規化一致で他概念と重複不可 | `--apply` 時の機械検査 |
| 候補由来検査 | accept は候補由来のみ | `--apply` 時の機械検査 |
| `input_terminology_sha256` の記録位置 | **`params` の中**（第 4 巡で訂正。第 3 巡は兄弟キーだった） | 採掘元辞書の追跡 |
| 対象研究数 | 3312（不変） | `studies` テーブルの全件（すべて Non-interventional study） |

## 候補生成の欠陥修正（第 4 巡、背景）

第 2 回検証（`docs/agent_report/202609141030_verifier_terminology_mining_round3.md`）で、
本番 FTS の NEAR(±3) 近接一致では `cardiac failure`（bigram）と
`failure cardiac failure`（trigram、"...カタログのある研究の title 末尾と outcomes 冒頭が
同じ語句を挟んで連結される" ことで実際に生成される n-gram）が同一の研究集合に一致することが
判明した。両者の `a` が等しいため `dedup_subsumed()` が「短い方は長い方に包含された冗長語」と
誤認し、**人間が読めば意味をなさないトークン重複トリグラムを残し、清潔な bigram の方を
削除していた**。第 4 巡はこれを `build_ngrams()` の時点で修正した:
同一トークンを 2 回以上含む n-gram（`("cardiac","failure","cardiac")` 等）は候補生成の
段階で一切作らない。回帰テスト `test_build_ngrams_drops_grams_with_a_repeated_token`
で固定した。

この修正と `TOP_K` の 30→60 拡大の効果を、第 2 回検証が名指しした 3 語で確認した:

| 語 | 概念 | 修正前の状況 | 修正後 |
|---|---|---|---|
| `cardiac failure` | heart_failure | `dedup_subsumed()` がトークン重複トリグラムを優先し候補から消失 | 候補に復帰（`a=31, b_new=7`）。TOP_K=60 内に到達し採用 |
| `diabetic ketoacidosis` | type_2_diabetes 等 | 同上（`ketoacidosis diabetic ketoacidosis` 等に置換され消失） | 候補に復帰。type_2_diabetes では `a=15, b_new=1` で TOP_K=60 内に到達し採用。type_1_diabetes/acute_kidney_injury/acute_pancreatitis でも候補に到達するが、概念境界・別概念判断により不採用 |
| `thromboembolic events` | venous_thromboembolism | 同上（`events thromboembolic events` 等に置換され消失） | **候補生成のバグは解消し、4 閾値を通過する候補として復帰する（`a=30, b_new=36`、TOP_K を外した全通過候補 497 件中の実測順位は 219 位（第 4 巡コードでの実測。第 5 巡の順列畳み込み後は 452 件中 200 位）。第 5 巡で 218 位という誤記載を訂正: 218 位は同値タイの語順反転双子 `events thromboembolic`）。ただし対数オッズ比が 3.074 と低く（`df_all=71` で非アンカー研究にも広く出現する一般的な AESI 語であるため）、`TOP_K=60` には到達しない。** これはバグではなく、この語が概念横断的に使われる一般的な安全性アウトカム語であることの正しい反映であり、下記「候補外の重要語」に記録する |

## 順列重複の畳み込み（第 5 巡、背景）

最終検証（`docs/agent_report/202609141130_verifier_terminology_mining_final.md` #9）で、
本番 FTS の NEAR(±3) 一致が語順に鈍感なため、同一トークン多重集合の異なる語順が
統計量（`a`/`df_all`/`b_new`）の完全に等しい別々の候補として TOP_K の枠を無駄に消費している
ことが指摘された（`atrial_fibrillation` の `apixaban dabigatran rivaroxaban` 系語順違い 4 通り、
候補全体の 5.6%＝159/2841 件）。第 5 巡は `mine_concept()` でアンカー研究から候補 n-gram を
集める際、同時に語順ごとの出現アンカー文書数（`gram_anchor_doc_counts`）を数え、
`collapse_permutation_grams()` で各多重集合から 1 代表（出現研究数最大、同数は辞書順で最初）
だけを残すよう変更した。回帰テスト
`test_collapse_permutation_grams_prefers_highest_anchor_count_then_alphabetical` で固定した。

効果は、候補総数 2841→2839（順列重複が畳み込まれ、空いた枠には次点の候補が昇格するため、
TOP_K=60 で頭打ちの 47 概念ではほぼ相殺される）。全 48 概念のうち 44 概念で候補集合の一部が
入れ替わった（新規候補 164 件、消失候補 166 件）。採用済み 23 語はいずれも自分自身の多重集合の
代表として生き残り、候補から脱落した語はなかった（`heart_failure` の `failure cardiac`
（`cardiac failure` の語順反転）は本ラウンドでも代表争いに負けて候補から消え、
`cardiac failure` 自身が引き続き代表として残る）。新規に浮上した 164 候補はいずれも
目視確認の結果、採否規則 1〜3 のいずれかに抵触する不採用相当だった（下記「採否規則」参照）。

## 採否規則（第 5 巡で改訂）

候補 n-gram を採用するのは、次のすべてを満たす場合に限る（機械検査ではなく人手判定の基準。
`--apply` が機械的に検査するのは略語規則・候補由来・概念境界・上限のみ）:

1. その概念と同じ臨床実体、またはその直接の発現・測定値・下位型であること（第 1 巡以来）。
2. **薬効群・治療手段・処置を指す語ではないこと**（例: `hypoglycemic`（血糖降下薬）、
   `anti-hypertensive`、`colectomy`、`peritoneal dialysis`、`antipsychotics`）。
3. **疾患横断の一般臨床指標ではないこと**（例: `eosinophil count`（COPD 等でも使う）、
   `relapse rate`（IBD 等でも使う）、`disability status`（疾患横断の機能評価）、
   `bone metastases`（腫瘍学一般の実体で前立腺癌特異的でない））。
4. **標本確認（第 5 巡で改訂）**: 採用候補ごとに、既存カバレッジ種語に一致しない新規一致研究
   （`b_new` の対象研究）のタイトルを最大 10 件（それ未満ならその全件）実際に確認し、
   **無関係な研究の割合が 30% 超なら不採用**にする。ただし**確認件数が 3 件以下のときは、
   無関係が 1 件でもあれば不採用**にする。確認結果は研究 ID と可否（relevant/unrelated）を
   `decisions.json` の当該語の `sample_checks` に記録する。**本規則は採用候補すべてに
   遡及適用する**（下記「規則 (c) の改訂と遡及適用」節）。

   旧規則（第 4 巡）の「2 件以上、または 30% 超」という絶対件数条項は、第 4 巡では
   新規判定した 4 語にしか適用されておらず、かつ小標本の非対称を生んでいた
   （`platelet counts`: 1 件中 1 件・100% は「2 件以上」を満たさず素通りする一方、
   `cardiac failure`: 7 件中 2 件・29% や `seizure`: 8 件中 2 件・25% のような良い語まで
   絶対件数条項に抵触しかねなかった）。第 5 巡はこれを撤回し、絶対件数と割合を
   小標本側で機械的に一致させた（3 件以下は 1 件でも不採用）。

`decisions.json` の各概念のエントリは次のスキーマを持つ:

```
{
  "reason_codes": {"r01": "定型理由文...", "r02": "..."},
  "<concept_id>": {
    "accept": ["term1", "term2"],
    "accept_provenance": {"term1": {"reason": "...", "source": "new|carried_over|supervisor", "origin_round": 3|4|5}},
    "reject": {
      "term_a": {"reason_code": "r01", "source": "carried_over", "origin_round": 4},
      "term_b": {"reason": "個別の理由文...", "source": "new", "origin_round": 5}
    },
    "abbreviation_checks": {"term": {"verdict": "accept", "studies": [...]}},
    "sample_checks": {"term": {"total_new_matches": N, "checked": N, "unrelated_count": k,
                                "unrelated_rate": k/N, "verdict": "accept|reject", "studies": [...]}}
  }
}
```

`source` は判定の由来（`new`=本巡で新規判定、`carried_over`=前巡の判定をそのまま引き継いだ、
`supervisor`=監督役が明示的に確定した判断）、`origin_round` は判定が最初に確定した巡番号。
`accept` は `apply_decisions()` の既存の読み取りコード（プレーン文字列のリストを期待する）を
変更しないため文字列のリストのまま維持し、由来情報は兄弟キー `accept_provenance` に持たせた
（`reject` は `apply_decisions()` が一切読まないフィールドなので、値を文字列からオブジェクトへ
直接変更した）。`decisions.json` 自体がこの由来の正本であり、一時的な突き合わせスクリプトを
リポジトリに残さなくても単独で判定の経緯を再現できる。

**`reason_codes`（第 5 巡で追加）**: `reject` の理由が複数の語で文字通り同一になる場合
（設計変更のたびに大量発生する定型の不採用理由。例: 「設計変更○○で新たに候補集合に浮上した
新規候補。個別に確認した結果、(a)〜(f) のいずれかに該当し不採用」）、その定型文をトップレベルの
`reason_codes` マップに 1 度だけ格納し、各 `reject` エントリは `reason_code`（+ 必要なら短い
補足 `note`）でそれを参照する。個別の説明が要る語（標本確認の実測値を含む理由など）は従来どおり
`reason` に自由文で残す（`reason_code` と `reason` は排他）。`reason_codes` は概念 ID ではない
予約キーであり、`apply_decisions()` は `RESERVED_TOP_LEVEL_KEYS` に含まれるキーを概念として
解釈せず読み飛ばす。効果はファイルサイズ 1.63 MB → 0.33 MB（-80%）。

## 規則 (c) の改訂と遡及適用（第 5 巡）

改訂した標本確認規則（上記）を**採用語 23 件すべて**に適用し直した。第 4 巡までは新規判定した
4 語（`hba1c`、`diabetic ketoacidosis`、`cardiac failure`、`minor congenital malformations`）
にしか `sample_checks` が記録されておらず、第 3 巡から引き継いだ残り 19 語は未検証のまま
採用されていた。実際に確認した結果:

| 概念 ← 採用語 | 新規一致 | 無関係 | 率 | 判定 |
|---|---:|---:|---:|---|
| `heart_failure` ← `cardiac failure` | 7 | 2 | 29% | **採用を維持**（`sample_checks` の 2 件を無関係に訂正。旧記録は 7 件すべて relevant としていたが誤り） |
| `chronic_kidney_disease` ← `haemodialysis` | 2 | 1 | 50% | **不採用に変更** |
| `depression` ← `depressive` | 2 | 1 | 50% | **不採用に変更** |
| `thrombocytopenia` ← `platelet counts` | 1 | 1 | 100% | **不採用に変更** |
| `pregnancy_outcomes` ← `small gestational age` | 1 | 1 | 100% | **不採用に変更** |

残り 18 語は無関係 0 件、または閾値を下回る（改訂規則でも採用維持。個別の内訳は下記
「標本確認表」節）。結果、採用語は **23 語 → 19 語**に、`chronic_kidney_disease`・
`depression`・`thrombocytopenia` の 3 概念は採用語ゼロになった。

## 監督役判断による不採用（第 2 回検証を受けた確定判断）

第 2 回検証は、第 3 巡で採用した 34 語のうち新規一致 75 件を全件タイトル確認し、
**40 件（53%）が誤検出**であることを実測した。うち 12 語は誤検出率 100%、
3 語は誤検出率 50%超だった。監督役はこれら 15 語をすべて不採用と確定した
（理由は `decisions.json` に「監督役判断: 第 2 回検証で誤検出率 <値> を実測」として記録、
`source="supervisor"`, `origin_round=4`）。

| 語（概念） | 実測誤検出率 | 誤検出の型 |
|---|---:|---|
| `asthma: eosinophil count` | 100%(4/4) | 疾患横断の一般臨床指標（COPD フェノタイピング指標） |
| `ulcerative_colitis: colectomy` | 100%(2/2) | 治療手段（代替手術）・別文脈の手技コード |
| `crohn_disease: fistula` | 100%(2/2) | 別疾患の合併症（AV シャント・穿孔パネル） |
| `multiple_sclerosis: disability status` | 100%(1/1) | 疾患横断の一般臨床指標 |
| `multiple_sclerosis: relapse rate` | 100%(2/2) | 別疾患（IBD）でも使う一般指標 |
| `chronic_kidney_disease: peritoneal dialysis` | 100%(2/2) | 除外基準としての言及（透析患者除外） |
| `chronic_kidney_disease: albumin creatinine` | 100%(1/1) | 検査値列挙の NEAR アーティファクト |
| `prostate_cancer: bone metastasis` | 100%(1/1) | NEAR アーティファクト（別疾患研究） |
| `prostate_cancer: bone metastases` | 100%(3/3) | 疾患横断の一般臨床指標（腫瘍学一般） |
| `pregnancy_outcomes: ectopic` | 100%(1/1) | 別文脈（異所性石灰化）への NEAR 誤爆 |
| `osteoporosis: bone mineral` | 100%(1/1) | NEAR アーティファクト・既存語との重複 |
| `epilepsy: febrile convulsions` | 100%(2/2) | 別分類（ILAE 上はてんかんでなく急性症候性発作） |
| `hypertension: hypertensive` | 75%(6/8) | 妊娠高血圧疾患（別概念）・曝露薬(anti-hypertensive)文脈 |
| `hypoglycaemia: hypoglycemic` | 75%(3/4) | 血糖降下薬(曝露)文脈 |
| `heart_failure: ejection fraction` | 60%(3/5) | 別疾患のモニタリング項目・除外基準 |

## 概念別の採否表（最終状態、第 5 巡）

`候補 N` は `data/terminology_candidates.json` の当該概念の候補数（`TOP_K=60` 以内。
第 5 巡の順列重複畳み込みにより一部概念は 60 未満）。`採用 x + 不採用 y = 候補 N` が
**全 48 概念で例外なく成立する**。`any差分`/`outcome差分` は `role=any`/`role=outcome` での
検索件数の前後差分（次節参照）。`chronic_kidney_disease`・`depression`・`thrombocytopenia`
は「規則 (c) の改訂と遡及適用」節の標本確認により、第 5 巡で採用語がゼロになった
（唯一の採用語がそれぞれ不採用に変わったため）。

| concept_id | A | 候補 N | 採用 | 不採用 | 採用語 | any差分 | outcome差分 |
|---|---:|---:|---:|---:|---|---:|---:|
| liver_injury | 81 | 60 | 2 | 58 | acute liver, liver injuries | +2 | +5 |
| type_2_diabetes | 169 | 60 | 2 | 58 | diabetic ketoacidosis, hba1c | +4 | +4 |
| type_1_diabetes | 48 | 60 | 0 | 60 | — | 0 | 0 |
| hypoglycaemia | 28 | 60 | 0 | 60 | — | 0 | 0 |
| atrial_fibrillation | 132 | 60 | 0 | 60 | — | 0 | 0 |
| heart_failure | 146 | 60 | 1 | 59 | cardiac failure | +7 | +3 |
| myocardial_infarction | 194 | 60 | 0 | 60 | — | 0 | 0 |
| stroke | 238 | 60 | 0 | 60 | — | 0 | 0 |
| intracranial_haemorrhage | 27 | 60 | 0 | 60 | — | 0 | 0 |
| gastrointestinal_bleeding | 41 | 60 | 0 | 60 | — | 0 | 0 |
| major_bleeding | 223 | 60 | 0 | 60 | — | 0 | 0 |
| venous_thromboembolism | 136 | 60 | 0 | 60 | — | 0 | 0 |
| hypertension | 152 | 60 | 0 | 60 | — (hypertensive は監督役判断で不採用) | 0 | 0 |
| dyslipidaemia | 38 | 60 | 1 | 59 | ldl c | +2 | +6 |
| chronic_kidney_disease | 60 | 60 | 0 | 60 | — (haemodialysis は第 5 巡で規則(c)遡及適用により不採用に変更) | 0 | 0 |
| acute_kidney_injury | 44 | 60 | 0 | 60 | — | 0 | 0 |
| copd | 157 | 60 | 0 | 60 | — | 0 | 0 |
| asthma | 187 | 60 | 0 | 60 | — (eosinophil count は監督役判断で不採用) | 0 | 0 |
| pneumonia | 78 | 60 | 0 | 60 | — | 0 | 0 |
| rheumatoid_arthritis | 153 | 60 | 0 | 60 | — | 0 | 0 |
| psoriasis | 67 | 60 | 0 | 60 | — | 0 | 0 |
| ulcerative_colitis | 90 | 60 | 0 | 60 | — (colectomy は監督役判断で不採用) | 0 | 0 |
| crohn_disease | 58 | 60 | 0 | 60 | — (fistula は監督役判断で不採用) | 0 | 0 |
| multiple_sclerosis | 107 | 60 | 0 | 60 | — (disability status/relapse rate は監督役判断で不採用) | 0 | 0 |
| migraine | 66 | 60 | 0 | 60 | — | 0 | 0 |
| epilepsy | 67 | 60 | 1 | 59 | seizure（febrile convulsions は監督役判断で不採用） | +8 | +7 |
| parkinson_disease | 16 | 19 | 0 | 19 | — | 0 | 0 |
| dementia | 31 | 60 | 1 | 59 | alzheimer | +1 | 0 |
| depression | 91 | 60 | 0 | 60 | — (depressive は第 5 巡で規則(c)遡及適用により不採用に変更) | 0 | 0 |
| schizophrenia | 25 | 60 | 0 | 60 | — | 0 | 0 |
| osteoporosis | 56 | 60 | 2 | 58 | osteoporotic, osteoporotic fractures（bone mineral は監督役判断で不採用） | +2 | +3 |
| multiple_myeloma | 50 | 60 | 0 | 60 | — | 0 | 0 |
| breast_cancer | 82 | 60 | 0 | 60 | — | 0 | 0 |
| lung_cancer | 95 | 60 | 0 | 60 | — | 0 | 0 |
| colorectal_cancer | 47 | 60 | 0 | 60 | — | 0 | 0 |
| prostate_cancer | 62 | 60 | 0 | 60 | — (bone metastasis/bone metastases は監督役判断で不採用) | 0 | 0 |
| hepatocellular_carcinoma | 21 | 60 | 0 | 60 | — | 0 | 0 |
| thrombocytopenia | 54 | 60 | 0 | 60 | — (platelet counts は第 5 巡で規則(c)遡及適用により不採用に変更) | 0 | 0 |
| neutropenia | 29 | 60 | 0 | 60 | — | 0 | 0 |
| acute_pancreatitis | 27 | 60 | 0 | 60 | — | 0 | 0 |
| anaphylaxis | 45 | 60 | 2 | 58 | anaphylactoid, anaphylactic | +5 | +5 |
| herpes_zoster | 56 | 60 | 0 | 60 | — | 0 | 0 |
| covid19 | 204 | 60 | 2 | 58 | sars cov 2, covid19 | +2 | +10 |
| serious_infection | 150 | 60 | 1 | 59 | progressive multifocal leukoencephalopathy | +2 | +2 |
| rhabdomyolysis | 15 | 60 | 0 | 60 | — | 0 | 0 |
| major_adverse_cardiovascular_events | 186 | 60 | 0 | 60 | — | 0 | 0 |
| fracture | 84 | 60 | 0 | 60 | — | 0 | 0 |
| pregnancy_outcomes | 410 | 60 | 4 | 56 | congenital malformations, live birth, congenital anomalies, minor congenital malformations（ectopic は監督役判断で不採用、small gestational age は第 5 巡で規則(c)遡及適用により不採用に変更） | +3 | +4 |
| **合計** | | **2839** | **19** | **2820** | | | |

`type_2_diabetes` と `heart_failure` の採用語は第 4 巡の新規判定（`diabetic ketoacidosis`,
`hba1c`, `cardiac failure`。「候補生成の欠陥修正」節参照）。`pregnancy_outcomes` の
`minor congenital malformations` も第 4 巡の新規判定。残り 15 語（`liver_injury` 2、
`dyslipidaemia` 1、`epilepsy` 1、`dementia` 1、`osteoporosis` 2、`anaphylaxis` 2、
`covid19` 2、`serious_infection` 1、`pregnancy_outcomes` 3）は第 3 巡からの引き継ぎ
（第 5 巡で `sample_checks` を新規に記録）。

### 概念境界による不採用（新規判定）

`hba1c` と `diabetic ketoacidosis` は複数概念の候補に出現したが、概念境界の排他制約
（正規化一致で他概念と重複不可）を維持する方針（第 2〜3 巡から継続）に従い、
`type_2_diabetes` 側にのみ登録した。

| 語 | 候補として出現した概念 | 登録先 | 他概念での扱い |
|---|---|---|---|
| `hba1c` | type_2_diabetes, type_1_diabetes, hypoglycaemia, acute_pancreatitis | type_2_diabetes | 他 3 概念は排他制約により不採用（`hypoglycaemia` は第 3 巡から引き継ぎ、他 2 概念は第 4 巡の新規判定） |
| `diabetic ketoacidosis` | type_2_diabetes, type_1_diabetes, acute_kidney_injury, acute_pancreatitis | type_2_diabetes | 他 3 概念は排他制約・別概念判断により不採用（`acute_pancreatitis` は第 3 巡から引き継ぎ、他 2 概念は第 4 巡の新規判定） |

`heart_failure` の `failure cardiac`（`cardiac failure` の語順反転、NEAR 由来の重複断片）も
候補に出現したが、独立した臨床用語ではないため不採用にした。

## 略語確認表（4〜5 文字の単一トークン）

| 概念 | 候補 | 新規一致件数（確認） | 確認結果 | Verdict |
|---|---|---|---|---|
| type_2_diabetes | `hba1c` | 4/4 | 1/4 が無関係（甲状腺機能低下症研究での併存症モニタリング言及）、残り 3/4 は糖代謝管理と直接関連する文脈（tirzepatide 体重研究、Metreleptin レジストリ、前立腺癌患者のメタボリックシンドローム研究）。誤検出率 25%（規則(c)の閾値未満） | accept |

研究 38431（ANAMET）は監督役が再確認した。HbA1c はメタボリックシンドローム判定基準の血糖成分として測定されており、検査値の単なる列挙ではなく糖代謝異常そのものを定義する文脈のため関連ありと確定した（第 2 回検証の「検査値列挙型」判定との相違はこの根拠で解消）。

`decisions.json` の `type_2_diabetes.abbreviation_checks.hba1c` に研究 ID・タイトル・可否を
全件記録した。**採用した** 4〜5 文字略語は `hba1c` のみである（第 5 巡で訂正: 候補集合
自体には 4〜5 文字の単一トークン語が 61 種類存在するが — 例: `aceis`, `ards`, `ascvd`,
`doac`, `dpp4`, `lmwh`, `nyha`, `pcsk9`, `t2dm` 等 — そのほとんどは薬効群・治療手段・
研究デザイン略語や疾患横断の一般指標であり採否規則 1〜3 で不採用になる。「候補化された
4〜5 文字略語は `hba1c` のみ」という第 4 巡の記述は事実誤認だった）。

## 標本確認表（採否規則 4、rule (c)、第 5 巡で 23 採用語すべてに拡張）

新規一致研究の最大 10 件（それ未満なら全件）を実際にタイトル確認した記録。
`decisions.json` の各語の `sample_checks` に同内容を記録した。第 4 巡までは新規判定した
4 語にしか記録がなかったが、第 5 巡で残り 19 語（引き継ぎ語）にも標本確認を実施し、
23 語全件が記録済みになった。`cardiac failure` は第 4 巡の記録に誤りがあり（44283・37938 を
relevant と誤記）、第 5 巡で訂正した。

| 概念 | 候補 | 新規一致 | 確認 | 無関係 | 誤検出率 | 判定 |
|---|---|---:|---:|---:|---:|---|
| liver_injury | `acute liver` | 2 | 2 | 0 | 0% | **採用** |
| liver_injury | `liver injuries` | 1 | 1 | 0 | 0% | **採用** |
| type_2_diabetes | `diabetic ketoacidosis` | 1 | 1 | 0 | 0% | **採用** |
| type_2_diabetes | `hba1c` | 4 | 4 | 1 | 25% | **採用** |
| heart_failure | `cardiac failure` | 7 | 7 | 2 | 29% | **採用**（第 5 巡で訂正: 旧記録は 7 件すべて relevant としていたが、44283(NEAR アーティファクト)・37938(AESI 部分集団定義) の 2 件は無関係） |
| dyslipidaemia | `ldl c` | 2 | 2 | 0 | 0% | **採用** |
| chronic_kidney_disease | `haemodialysis` | 2 | 2 | 1 | 50% | **不採用**（第 5 巡で変更。103543 SILITOX リチウム中毒研究が治療手段の言及で採否規則2に抵触） |
| epilepsy | `seizure` | 8 | 8 | 2 | 25% | **採用**（103744・16907 が除外基準/ベースライン併存症で無関係、残り 6 件は AESI として正当） |
| dementia | `alzheimer` | 1 | 1 | 0 | 0% | **採用** |
| depression | `depressive` | 2 | 2 | 1 | 50% | **不採用**（第 5 巡で変更。103358 が抗うつ薬曝露の記述で採否規則2に抵触） |
| osteoporosis | `osteoporotic` | 2 | 2 | 0 | 0% | **採用** |
| osteoporosis | `osteoporotic fractures` | 2 | 2 | 0 | 0% | **採用** |
| thrombocytopenia | `platelet counts` | 1 | 1 | 1 | 100% | **不採用**（第 5 巡で変更。42149 D:A:D 研究の検査値列挙で、血小板減少症を測定していない） |
| anaphylaxis | `anaphylactoid` | 1 | 1 | 0 | 0% | **採用** |
| anaphylaxis | `anaphylactic` | 4 | 4 | 1 | 25% | **採用**（49679 は既往歴を理由とする除外基準で無関係、残り 3 件は AESI として正当） |
| covid19 | `sars cov 2` | 1 | 1 | 0 | 0% | **採用**（RSV 研究での共感染病原体列挙だが、共感染有病率が実際の推定対象） |
| covid19 | `covid19` | 1 | 1 | 0 | 0% | **採用**（TARGET EU の nested case study の一つとして COVID-19 を扱う） |
| serious_infection | `progressive multifocal leukoencephalopathy` | 2 | 2 | 0 | 0% | **採用** |
| pregnancy_outcomes | `congenital malformations` | 1 | 1 | 0 | 0% | **採用** |
| pregnancy_outcomes | `small gestational age` | 1 | 1 | 1 | 100% | **不採用**（第 5 巡で変更。24336 SGA 児への GH 治療安全性研究。SGA はコホート定義であり妊娠アウトカムとして測定されていない） |
| pregnancy_outcomes | `live birth` | 1 | 1 | 0 | 0% | **採用** |
| pregnancy_outcomes | `congenital anomalies` | 1 | 1 | 0 | 0% | **採用** |
| pregnancy_outcomes | `minor congenital malformations` | 1 | 1 | 0 | 0% | **採用** |
| pregnancy_outcomes | `birth weight` | 2 | 2 | 1 | 50% | **不採用**（第 4 巡から。DARWIN EU 新生児けいれん研究では出生体重がベースライン記述に留まる） |
| pregnancy_outcomes | `eclampsia` | 1 | 1 | 1 | 100% | **不採用**（第 4 巡から。唯一の新規一致が高血圧患者の大腸過形成・消化器癌研究で妊娠と無関係） |

`eclampsia`/`birth weight` の不採用は、`eclampsia` を `pregnancy_outcomes` に残すという
第 2〜3 巡の設計判断（境界調整）自体を覆すものではない。`eclampsia` は既に
`english_terms`/既存の語で相応にカバーされており、**今回追加で拾える新規一致 1 件だけが
たまたま無関係だった**ため、規則(c)の標本確認基準を機械的に適用した結果である。

`chronic_kidney_disease: haemodialysis`・`depression: depressive`・
`thrombocytopenia: platelet counts`・`pregnancy_outcomes: small gestational age` の
4 語は、改訂した標本確認規則（確認 3 件以下は無関係 1 件でも不採用、または割合 30% 超で
不採用）を適用した結果、第 3 巡以来の採用が第 5 巡で不採用に転じた。個別の研究 ID・
判定根拠は上表と「規則 (c) の改訂と遡及適用」節、および `decisions.json` の各語の
`sample_checks` を参照。

## 検索件数の前後比較と `b_new` の整合（全 48 概念）

`Service.search_studies(headword, limit=1, darwin_only=False, role="any"|"outcome")` を
in-process で呼び、`EMA_TERMINOLOGY_PATH` を HEAD 辞書（before）→ 本巡適用後の辞書（after）
に切り替えて `total_matches` を比較した（DB は不変）。代表 3 概念は CLI
（`uv run ema-rwe search "<見出し語>" --all-studies --role any`）でも同値を確認した
（2 型糖尿病 312→316、心不全 146→153、妊娠 466→470）。

`sum_b_new` は採用語の `terminology_candidates.json` 上の `b_new` の単純合計
（一致集合が重複しうるため合計は上振れしうる）。

| concept_id | any(前) | any(後) | any差分 | sum(b_new) | 整合 |
|---|---:|---:|---:|---:|---|
| liver_injury | 179 | 181 | 2 | 3 | 差分<sum（`acute liver`⊃`liver injuries` の一致集合重複） |
| type_2_diabetes | 312 | 316 | 4 | 5 | 差分<sum（唯一の新規一致 47613 が本番 `expand()` のコード・日本語見出し語展開により HEAD 辞書でも既に検索結果に含まれていたため。「一致集合重複」ではない。下記「type_2_diabetes 行の訂正」参照） |
| type_1_diabetes | 310 | 310 | 0 | 0 | 完全一致 |
| hypoglycaemia | 28 | 28 | 0 | 0 | 完全一致 |
| atrial_fibrillation | 132 | 132 | 0 | 0 | 完全一致 |
| heart_failure | 146 | 153 | 7 | 7 | 完全一致 |
| myocardial_infarction | 210 | 210 | 0 | 0 | 完全一致 |
| stroke | 244 | 244 | 0 | 0 | 完全一致 |
| intracranial_haemorrhage | 259 | 259 | 0 | 0 | 完全一致 |
| gastrointestinal_bleeding | 259 | 259 | 0 | 0 | 完全一致 |
| major_bleeding | 258 | 258 | 0 | 0 | 完全一致 |
| venous_thromboembolism | 153 | 153 | 0 | 0 | 完全一致 |
| hypertension | 176 | 176 | 0 | 0 | 完全一致 |
| dyslipidaemia | 54 | 56 | 2 | 2 | 完全一致 |
| chronic_kidney_disease | 145 | 145 | 0 | 0 | 完全一致（第 5 巡: `haemodialysis` 不採用のため差分なし） |
| acute_kidney_injury | 87 | 87 | 0 | 0 | 完全一致 |
| copd | 214 | 214 | 0 | 0 | 完全一致 |
| asthma | 187 | 187 | 0 | 0 | 完全一致 |
| pneumonia | 92 | 92 | 0 | 0 | 完全一致 |
| rheumatoid_arthritis | 246 | 246 | 0 | 0 | 完全一致 |
| psoriasis | 101 | 101 | 0 | 0 | 完全一致 |
| ulcerative_colitis | 114 | 114 | 0 | 0 | 完全一致 |
| crohn_disease | 90 | 90 | 0 | 0 | 完全一致 |
| multiple_sclerosis | 107 | 107 | 0 | 0 | 完全一致 |
| migraine | 72 | 72 | 0 | 0 | 完全一致 |
| epilepsy | 78 | 86 | 8 | 8 | 完全一致 |
| parkinson_disease | 18 | 18 | 0 | 0 | 完全一致 |
| dementia | 41 | 42 | 1 | 1 | 完全一致 |
| depression | 105 | 105 | 0 | 0 | 完全一致（第 5 巡: `depressive` 不採用のため差分なし） |
| schizophrenia | 37 | 37 | 0 | 0 | 完全一致 |
| osteoporosis | 83 | 85 | 2 | 4 | 差分<sum（`osteoporotic`⊃`osteoporotic fractures` の一致集合重複） |
| multiple_myeloma | 52 | 52 | 0 | 0 | 完全一致 |
| breast_cancer | 637 | 637 | 0 | 0 | 完全一致 |
| lung_cancer | 638 | 638 | 0 | 0 | 完全一致 |
| colorectal_cancer | 636 | 636 | 0 | 0 | 完全一致 |
| prostate_cancer | 636 | 636 | 0 | 0 | 完全一致 |
| hepatocellular_carcinoma | 649 | 649 | 0 | 0 | 完全一致 |
| thrombocytopenia | 59 | 59 | 0 | 0 | 完全一致（第 5 巡: `platelet counts` 不採用のため差分なし） |
| neutropenia | 31 | 31 | 0 | 0 | 完全一致 |
| acute_pancreatitis | 30 | 30 | 0 | 0 | 完全一致 |
| anaphylaxis | 142 | 147 | 5 | 5 | 完全一致 |
| herpes_zoster | 61 | 61 | 0 | 0 | 完全一致 |
| covid19 | 206 | 208 | 2 | 2 | 完全一致 |
| serious_infection | 165 | 167 | 2 | 2 | 完全一致 |
| rhabdomyolysis | 16 | 16 | 0 | 0 | 完全一致 |
| major_adverse_cardiovascular_events | 396 | 396 | 0 | 0 | 完全一致 |
| fracture | 84 | 84 | 0 | 0 | 完全一致 |
| pregnancy_outcomes | 466 | 469 | 3 | 4 | 差分<sum（採用 4 語の一致集合が部分的に重複。`small gestational age` 不採用のため第 4 巡の 466→470/+4/5 から縮小） |

**結果**: 48 概念中 44 概念で `any差分 == sum(b_new)` が完全一致。一致しない 4 概念
（`liver_injury`、`type_2_diabetes`、`osteoporosis`、`pregnancy_outcomes`）は
いずれも `any差分 < sum(b_new)`（一度も `any差分 > sum(b_new)` にはならない）。
`osteoporosis`/`liver_injury`/`pregnancy_outcomes` の原因は採用語が複数ある概念で
一致集合が部分的に重複していること（集合演算として当然の帰結）。`type_2_diabetes` は
性質が異なる（次節「type_2_diabetes 行の訂正」参照）。

### type_2_diabetes 行の訂正（第 5 巡）

第 4 巡の文書は `type_2_diabetes` の差分<sum を「`diabetic ketoacidosis`/`hba1c` の
一致集合重複」と説明していたが、これは**誤り**だった。両語の新規一致集合は互いに素
（`diabetic ketoacidosis` の新規一致は `{47613}`、`hba1c` の新規一致は
`{1000000252, 1000000983, 1000001052, 38431}`）であり、共通の研究 ID はない。
実際の原因は、`diabetic ketoacidosis` の唯一の新規一致 `47613` が、**HEAD 辞書の時点で
既に本番検索の `type_2_diabetes` 検索結果に含まれていた**ことにある（本番検索は
`ema_rwe.terminology.expand()` で ICD/ATC コードと日本語見出し語の content word も
OR に足すため、`b_new` の算出基準（`english_terms + related_terms` のみへの一致）より
本番検索の実際のカバレッジの方が広い）。

このことは `b_new` の設計上の主張（「本番検索で実際に候補を増やす語」を意味する、設計変更 10）
に**限定を要する**ことを示す: `b_new >= 1` は候補の n-gram 自体が `english_terms +
related_terms` に一致しない研究をカタログ本文レベルで捉えていることを保証するが、
`expand()` が追加で展開するコード・日本語見出し語ぶんの本番検索カバレッジまでは
考慮していない。したがって「`b_new` が本番検索の実増分に一致する」という主張は、
**`expand()` のコード・日本語見出し語展開による増分を除けば**という限定付きで成立する。
他の 3 概念（`liver_injury`/`osteoporosis`/`pregnancy_outcomes`）の差分<sum は、
この限定とは無関係な、採用語どうしの一致集合の真の重複である。

## 候補外の重要語（TOP_K=60 に届かない、または構造的に候補化されない語）

| 概念 | 候補外の語 | 実測理由 |
|---|---|---|
| venous_thromboembolism | `thromboembolic events` | **（第 4 巡で訂正、順位を第 5 巡でさらに訂正）** 候補生成バグ（トークン重複 n-gram による置換）は解消し、4 閾値を通過する候補として復帰する（`a=30, b_new=36`、TOP_K を外した全通過候補 497 件中 **219 位**（第 4 巡コードでの実測。第 5 巡の順列畳み込み後は 452 件中 200 位）。第 4 巡は 218 位と誤記していたが、218 位は同値タイの語順反転双子 `events thromboembolic` である）。対数オッズ比 3.074 が低いのは、この語が VTE 以外の多数の安全性研究でも汎用的に使われる一般的な AESI 語であり（`df_all=71` のうち非アンカー研究が 41 件）、統計的に見て概念特異性が低いという正しい反映である。「自己参照的カバレッジ相殺」という第 3 巡の説明は誤りだった（`b_new=36` であり相殺は起きていない）。第 5 巡で `b_new` を加味した順位づけを検討したが、上位 60 に入れるには `b_new` がそれ以上の候補 49 件（第 5 巡の畳み込み後は 41 件）（うち大半が薬効群・治療手段・疾患横断の一般指標）を先に判定者に通す必要があり、費用対効果が悪いため採用しなかった（下記「未解決事項」参照） |
| heart_failure | `congestive heart failure`（フルフレーズ） | 自己参照的カバレッジ相殺（`heart failure` がアンカー）。第 4 巡では個別再検証していない（スコープ外） |
| dyslipidaemia | `familial hypercholesterolaemia` | 自己参照的カバレッジ相殺（`hypercholesterolaemia` がアンカー）。第 4 巡では個別再検証していない |
| multiple_myeloma | `relapsed refractory multiple myeloma` | 自己参照的カバレッジ相殺。第 4 巡では個別再検証していない |
| breast_cancer | `metastatic breast cancer` | 自己参照的カバレッジ相殺。第 4 巡では個別再検証していない |
| colorectal_cancer | `metastatic colorectal cancer` | 自己参照的カバレッジ相殺。第 4 巡では個別再検証していない |
| prostate_cancer | `castration-resistant prostate cancer` | 自己参照的カバレッジ相殺（`prostate cancer` がアンカー）。第 4 巡では個別再検証していない |
| thrombocytopenia | `thrombosis with thrombocytopenia syndrome` | n-gram 上限 3 語による構造的排除（4 語）。第 4 巡でも `MAX_NGRAM=3` は不変のため引き続き候補化不能 |
| osteoporosis / fracture | `hip fracture` 等 | 自己参照的カバレッジ相殺（`fracture` がアンカー）。bare `fracture` で実質充足 |
| multiple_sclerosis | `relapsing-remitting multiple sclerosis` | n-gram 上限 3 語（ハイフン分割後 4 トークン）。構造的に候補化不能 |

`familial hypercholesterolaemia` 等、「第 4 巡では個別再検証していない」と付記した行は、
第 3 巡までの推定理由をそのまま引き継いだもので、第 4 巡の指示範囲（`thromboembolic events`/
`cardiac failure`/`diabetic ketoacidosis`/`hba1c` の再確認）には含まれていなかった。
次巡で再検証する場合は同じ手法（`TOP_K` を外した全候補の順位・対数オッズ比の実測）を使うこと。

## 既知の構造的限界

- **n-gram 上限（3 語）による構造的な取りこぼし**: `small for gestational age`（4 語）、
  `thrombosis with thrombocytopenia syndrome`（4 語）、`relapsing-remitting multiple
  sclerosis`（ハイフン分割後 4 トークン）のような 4 語以上の臨床用語は、`MAX_NGRAM=3` の
  もとでは原理的に候補になり得ない。
- **自己参照的カバレッジ相殺**: 概念の `english_terms` 自体が短い一般語（`fracture` の
  `fracture`、`heart_failure` の `heart failure` 等）を含む場合、その語を含むより具体的な
  下位表現は、対象文書が既にアンカー（bare の概念語）でカバーされているため `b_new` が
  常にゼロになり、候補として決して浮上しない。実害は概念の bare な語で既に検索できている分
  小さいが、次回の設計見直しの候補としたい。
- **`TOP_K` ランキングの限界**: 対数オッズ降順のランキングは、概念特異性が高くアンカー内出現
  頻度が高い語を優先するため、`thromboembolic events` のように新規一致件数（`b_new`）は
  大きくても概念横断的に使われる語は上位に来ない。これは概念の特異性を測るという設計の
  意図どおりの挙動であり、バグではない。
- **`b_new` は本番検索の実増分の下限に過ぎない**（第 5 巡で判明。上記「type_2_diabetes 行の
  訂正」参照）: `b_new` は候補の n-gram が `english_terms + related_terms` に一致しない
  研究の件数だが、本番検索は `expand()` でコード・日本語見出し語も OR に足すため、
  実際の本番カバレッジは `b_new` が示すより広いことがある。`b_new >= 1` は「候補として
  拾う価値がある」ことの十分条件ではあるが、「採用後に本番検索の総ヒット数が必ず増える」
  ことの保証ではない。

## 未解決事項

- `thromboembolic events` は `TOP_K=60` でも候補に届かない（前節）。`b_new=36` は
  `venous_thromboembolism` の全通過候補 452 件中 `b_new` 順 42 位（上位 1 割）で、採用を検討する価値が
  ある語だが、対数オッズ順では 200 位で `TOP_K=60` に届かない。第 5 巡で `b_new` 加味の
  順位づけを数値で検討したが、費用対効果が悪く不採用と判断した（前節）。概念ごとの
  `TOP_K` 個別調整は未検討のまま残る。
- `familial hypercholesterolaemia`・`congestive heart failure` 等、第 3 巡の「候補外の重要語」
  として記録されていた語のうち、`thromboembolic events`/`cardiac failure`/
  `diabetic ketoacidosis`/`hba1c` 以外は第 4〜5 巡で個別再検証していない。
- **標本確認の小標本での非対称は改訂規則（第 5 巡）でおおむね解消したが、境界事例は残る**:
  `heart_failure: cardiac failure` は新規一致 7 件中 2 件（29%）が無関係でも採用を維持する
  一方、`thrombocytopenia: platelet counts`（1 件中 1 件）のように確認件数が少ない語は
  無関係が 1 件あるだけで不採用になる。これは意図した設計（確認件数が少ないほど 1 件の
  誤りが致命的になりやすいため厳格化する）だが、境界（3 件以下／30% 超）の妥当性自体は
  実証的に検証していない。
- **1 型／2 型糖尿病で共有する語の設計（第 5 巡で追加）**: `diabetic ketoacidosis`・`hba1c`
  はいずれも 1 型・2 型糖尿病に共通する急性合併症・血糖コントロール指標であり、
  臨床的にはどちらか一方の概念に排他的に属する実体ではない。概念境界の排他制約
  （正規化一致で他概念と重複不可）により機械的に `type_2_diabetes` 側へ一本化しているが、
  「1 型糖尿病」で検索したユーザーはこれらの語からの拡張を受けられない。
  本番検索の実増分（`diabetic ketoacidosis` は 0 件、上記「type_2_diabetes 行の訂正」参照）
  を踏まえると実害は小さいが、糖尿病系概念（type_1_diabetes/type_2_diabetes/
  hypoglycaemia）で語を共有できる設計（例: 概念境界の排他制約を緩め、複数概念への
  同時登録を許す）は、片方の概念に押し込む現行の運用の代替として次巡以降で検討する
  余地がある。本巡ではこの設計変更を行わず、`diabetic ketoacidosis`/`hba1c` は
  `type_2_diabetes` に置いたまま維持する（監督役確定判断）。

## 改訂履歴

- 第 1 巡（2026/09/14 07:58〜）: `contains()` 部分文字列一致で 48 概念を初回採掘（64 語採用）。
- 第 2 巡（〜08:52）: アンカーを `english_terms` のみに限定、略語規則・概念境界検査・候補由来
  検査を新設。
- 第 3 巡（〜10:04）: 一致判定を本番 FTS（`fts_match()`）に統一、監督役の明示 reject 10 件を
  反映（34 語採用）。
- 第 2 回検証（10:30〜10:54）: 採用語 34 件中 40/75 件（53%）が誤検出と判明。
  `dedup_subsumed()` の構造的欠陥（トークン重複 n-gram による清潔な語の置換）、`TOP_K=30` の
  取りこぼしを指摘。
- 第 4 巡（〜11:18）: 15 語を監督役判断で不採用（誤検出率 60〜100%）、候補生成の欠陥修正
  （トークン重複 n-gram 除外）、`TOP_K` を 60 に拡大、標本確認規則（2 件以上/30%超で不採用）
  を新設。採用語は 23 語（引き継ぎ 19 + 新規 4）。
- 最終検証（11:20〜11:36）: 標本確認規則が新規 4 語にしか適用されておらず引き継ぎ 19 語中
  4 語が実測で規則超過、`cardiac failure` の `sample_checks` に 2 件の誤記、文書に事実誤り
  2 件（略語確認表の注・`type_2_diabetes` 行の注記）、順列重複候補 159 件（5.6%）、
  `thromboembolic events` の順位誤記（実測 219 位を 218 位と記載）を指摘。
- 第 5 巡・最終（本ドキュメント）: 標本確認規則を改訂（3 件以下は無関係 1 件でも不採用、
  それ以外は 30% 超で不採用）し採用語 23 件全件に遡及適用、`cardiac failure` の
  `sample_checks` を訂正、`build_ngrams()` の順列重複畳み込みを新設（回帰テスト 1 本）、
  `decisions.json` の不採用理由を `reason_codes` マップに圧縮（1.63 MB→0.33 MB）、
  文書の事実誤り 2 件と手順 1・順位を訂正。最終的な採用語は **19 語**（引き継ぎ 15 + 新規 4）。
  `chronic_kidney_disease`/`depression`/`thrombocytopenia` は採用語ゼロになった。
