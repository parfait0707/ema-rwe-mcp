# 検索精度の改善と MCP コンテキスト削減

## 与えられた指示

1. 実走で見つけた改善案のうち「othersが大きすぎる」以外を、CSV・PDF の実データを踏まえたベストプラクティスで実装する。
2. MCP 構成（案A）は継続しつつ、コンテキスト消費を抑えるリファクタリングを検討・実装する。

## 確認した決定（AskUserQuestion）

- 複数語の一致は近傍一致（FTS5 NEAR、3 語以内）。
- 日本語疾患名辞書（WHO ICD-10 2019 分類見出し、出典 URL 付き、unverified）を `data/terminology.json` として同梱し既定にする。
- EMA 公式医薬品辞書を取得し `data/ema-medicines.json` としてコミットする。

## 実装・変更内容

- `terminology.py`: `search_phrases`（検索語を語群に分解。4 語以下は 1 群、5 語以上は単語）。`search_units` は PDF 検索用に維持し、共通部分を `_content_words` に抽出。同梱辞書を既定パスに。
- `storage.py`: スキーマ v4。FTS に `condition`／`outcomes`／`exposure`／`objective` 列を追加。`fts_match` が NEAR と列限定（`{cols}: (...)`）を組み立てる。`ROLE_COLUMNS`、`BM25_WEIGHTS`。import で公式ヘッダから役割列を取り込む（`ALIASES`、`ROLE_EXTRA_COLUMNS`）。旧 DB は起動時に FTS を再構築。
- `domain.py`: `Study` に `conditions`／`outcomes`／`exposures`／`objective`。
- `selection.py`: `compact`、`facets.conditions`、`candidates`／`candidates_listed`／`max_listed_candidates`、`next_action` の文言。
- `service.py`: `search_studies(role, detail)`、`compare_protocols(role, study_ids)`、`expansion_summary`、`_catalogue_summary`。
- `config.py`: `EMA_MAX_LISTED_CANDIDATES`、`default_terminology_path`。
- `cli.py`: `--role`、`--detail`、`--study-id`。
- `mcp/server.py`: `instructions` を要点のみに短縮、docstring を 1〜3 行化、`role`／`detail`／`study_ids` を公開。手順は `docs/mcp-workflow.md` に移動。
- データ: `data/terminology.json`（45 概念）、`data/ema-medicines.json`、DB 再構築。`.gitignore` に例外追加。
- テスト: `tests/test_screening.py`（9 件）。全 157 件合格。
- 文書: README、AGENTS.md、docs/comparisons.md、docs/clinical-search.md、docs/spec/v0.4.md、docs/validation.md、.env.example。

## 次のアクション・懸念事項

- NEAR の距離 3 は経験則。実運用で見逃しが出たら距離か語順の扱いを見直す。
- 辞書のコードは分類見出しのみで人手確認は未実施。臨床の専門家による確認を推奨。
- `others` の内訳細分化は見送り（ユーザー判断）。
- MCP クライアント上の実会話（`needs_narrowing` → 質問 → `study_ids`）は再起動後の再確認が必要。
