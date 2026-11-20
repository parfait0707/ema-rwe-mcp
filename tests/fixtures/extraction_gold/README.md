# 抽出の正解データ

題材ごとに 1 ファイルです（spec v1.20、v1.21）。どれも同じ 4 つのプロトコルを、人が読んで列挙したもので、プロトコルの本文は入っていません。

| ファイル | 内容 | 採点で読む解析のブロック | 下限（回答、解析） |
|---|---|---|---|
| `eligibility.json` | 組入れ基準と除外基準、51 項目 | `cohort.inclusion_criteria`、`cohort.exclusion_criteria` | 90%、70% |
| `outcomes.json` | アウトカムの定義（挙げられたアウトカム、同定に使う登録・記録、コード、感度分析での別定義、SAP に委ねる点、別の節に書かれた定義）、31 項目 | `outcomes`、`disease_definitions` | 80%、60% |

項目は、自分の言葉での説明、物理ページ、手元の PDF からその箇所を見つける短いパターン、採点で受け入れる鍵となる語だけを持ちます。

| いつ | 何を実行するか | 何を確かめるか |
|---|---|---|
| スクリプトを変えるたび（push の前） | `uv run python scripts/check.py`（`gold` の段階） | 全項目が、正解のページで見つかり、読み手に渡る範囲にあり、質問で返る範囲に入り、根拠として受理される。LLM もネットワークも使わない |
| 月ごとのカタログの更新 | `scripts/build_catalogue_snapshot.py`（自動で実行） | 上と同じ検査と、正解の研究が新しいカタログに Non-interventional study として載っていること |
| 月ごとの更新、アプリのリリース | `uv run python scripts/extraction_eval.py catalogue --online` | EMA で、最新のプロトコルが正解の文書のままか（研究ごとに 1 回、間隔を空けて） |
| 月ごとの更新、アプリのリリース | `uv run python scripts/extraction_eval.py run`（内部 LLM）、または呼出元抽出の後に `grade` | 読み手の抽出が、正解の項目をどれだけ含むか（下限は上の表） |

## 最初に 1 回

```bash
uv run python scripts/extraction_eval.py fetch
```

正解の 4 つのプロトコルを EMA から取得し、プロトコルの保存先（checkout では `data/protocols/`。git の対象外）に置きます。1 件ずつ 30 秒空けるので、2 分ほどかかります。429 で止まったら、時間を置いて同じコマンドを実行します（取得済みは飛ばします）。取得は DB の複製で行うので、同梱の `data/ema.sqlite3` は変わりません。

取得していない clone から push するときは、`uv run python scripts/check.py --allow-missing-gold` が手元にある分だけを確かめます（pre-push hook は `--require` で実行するので、取得するか `git push --no-verify` を使います）。

## 採点（LLM を使う）

```bash
# 内部 LLM（LLM_MODEL など）を設定してあるとき：抽出、質問別回答、採点を続けて行う
uv run python scripts/extraction_eval.py run
# 呼出元抽出（既定）：MCP クライアントで、4 つの研究に各ファイルの質問（question）で
# analyze_protocol を最後まで行った後（質問ごとに 1 回）
uv run python scripts/extraction_eval.py grade
```

`grade` は、`EMA_DB_PATH` の DB に保存済みの質問別回答と解析を読みます。

## 検査が失敗したとき

- `check` の失敗：直前の変更（PDF の parser、節の役割、読む順序、検索、根拠の検証）が、その項目を失わせています。表示される項目の ID と理由から、変更を直します。正解の側を変えるのは、プロトコルを読み直して誤りを確かめたときだけにします。
- `catalogue` の失敗：正解の研究がカタログから消えたか、Non-interventional study でなくなっています。エクスポートを確かめます。
- `catalogue --online` の `LATEST PROTOCOL CHANGED`：新しい版を読み、`sha256`、`version`、`pages`、項目を改めます。
- `grade` の失敗：`not in the answer` の項目を、保存済みの回答と見比べます。読み手が触れているのに `accept` の語が狭いだけなら、語を足します。

## 項目を足す、プロトコルを足す

既存のファイルに項目を足すか、新しい題材のファイル（`question`、`analysis_fields`、`kinds`、`minimum`、`protocols`）を足します。フォルダの全ファイルが、検査と採点の対象になります。プロトコルを読み、基準ごと（アウトカムごと）に 1 項目を書きます。付録のコードリストのように、質問への最初の検索に入らなくてよい項目は、`beyond_first_search: true` とします。`pattern` は、そのページでその基準の行だけに当たる数語（45 文字以内。文を写さない）。`accept` は、読み手が別の言い方で書いても当たる鍵となる語。`uv run python scripts/extraction_eval.py check` が全項目で通ることを確かめます。

## 範囲

組入れ・除外基準とアウトカムの定義だけ、英語で文字層のある 4 つのプロトコルだけです。データソース、曝露、共変量、コードリストの全体は採点しません。アウトカムの正解は、アウトカムの節と、主要アウトカムのコードの 1 行までで、付録の表の全体は含みません。`accept` は緩く（年齢の基準は「18 years」で受け入れる）、採点は「その基準に触れているか」を見るもので、内容の正しさの保証ではありません。
