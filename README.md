# EMA RWE MCP

EMA Catalogueの **Non-interventional study** を検索し、Study documentsの最新プロトコルPDFから、研究デザイン・疾患定義・データソースを出典付きで抽出・再利用するPython MCPサーバーです。Core、CLI、MCPを分離しています。

`docs/spec/v0.1.md` と追加要件に基づくv0.3実装です。追加探索の仕様は [docs/spec/v0.2.md](docs/spec/v0.2.md)、医薬品・コード検索は [docs/clinical-search.md](docs/clinical-search.md) を参照してください。通常検索はSQLite FTS5/BM25だけで実行し、PDFは選択した研究についてのみ取得します。

## セットアップ

このチェックアウト用のCodex MCP設定は [`.codex/config.toml`](.codex/config.toml)、開発ルールは [`AGENTS.md`](AGENTS.md) です。[Codex設定ガイド](docs/codex-setup.md)を参照してください。

日本語の疾患名から英語の関連語・医療コードへ検索を広げる機能を追加しています。`J84.9`／`J849`などの表記揺れ、呼出元が指定するコード、独自辞書、LLMによる候補提案に対応します。具体例と網羅性の制約は [臨床概念・医療コード検索](docs/clinical-search.md) を参照してください。

Python 3.12以上とuvを利用します。

```powershell
uv venv --python 3.12
uv sync --extra dev
$env:EMA_DB_PATH = "E:/codex/rwd-catalogue-mcp/data/ema.sqlite3"
$env:EMA_CACHE_DIR = "E:/codex/rwd-catalogue-mcp/cache/http"
.venv/Scripts/ema-rwe --help
```

別の場所にcloneした場合はパスを置き換えてください。Windows以外では `.venv/bin/ema-rwe` を使用します。環境変数未設定時はOSのユーザーデータ／キャッシュディレクトリを利用するので、MCP起動時の作業ディレクトリに依存しません。`.env.example` は設定例で、自動読込はしません。

## 1. 公式CSVで検索対象を登録

[EMA検索ページ](https://catalogues.ema.europa.eu/search?f%5B0%5D=content_type%3Adarwin_study)のStudies用Exportから、必要な研究のCSVをブラウザでダウンロードします。

```powershell
.venv/Scripts/ema-rwe import-csv ./export-data.csv
.venv/Scripts/ema-rwe search "opioid cohort" --limit 5
.venv/Scripts/ema-rwe search "diabetes" --all-studies
```

- `Non-interventional study` / `Non-interventional` と明示された研究だけ取り込みます。種別不明・介入研究は除外します。
- URL中の `content_type:darwin_study` は研究レコード種別です。DARWIN EU研究限定とは異なります。`darwin_only=true`（CLIの既定）は独立したDARWIN EUフラグを判定します。
- 元CSVのバイト列、SHA256、ファイル名、取込時刻を保存します。研究ID単位のupsertであり、今回のCSVにない既存研究は削除しません。
- 必須列は `Study ID`, `Official title and acronym`, `Study type`。別名の対応は `storage.py` の `ALIASES` を参照してください。不一致はエラーになり、黙って0件登録しません。
- 列名が異なる場合は `--column-map ./columns.json` を使用します。例: `{"study_id":"Study identifier","title":"Title","study_type":"Type"}`。公式CSV実ファイルは今回の環境では未取得のため、実運用開始時に列対応を確認してください。
- `Data sources (types)` / `Data source type` 列を取り込みます。複数値の区切りは `|`・`;`・改行です。値内部のカンマは分割しません。
- 原本CSVに連絡先が含まれる場合があります。原本は検索対象から分離され、連絡先専用列をDB／FTS／検索結果には入れません。

2026-09-12に確認した [robots.txt](https://catalogues.ema.europa.eu/robots.txt) は `/search/` 配下を禁止しています。Exportの自動ダウンロード・全検索ページのクロールは実装していません。CSVなしでも、既知のStudy IDを `study` / `analyze` に渡すとその研究を登録できます。

## 2. プロトコルの抽出と再利用

```powershell
.venv/Scripts/ema-rwe study 1000000479
.venv/Scripts/ema-rwe protocol 1000000479
.venv/Scripts/ema-rwe analyze 1000000479
```

抽出は2つの方式を提供します。

**追加APIキーなし（既定）**: `analyze_protocol` が関連セクション・物理PDFページ番号・抽出JSON Schema・fingerprintを返します。呼び出し元のClaude Code/Codexが全バッチを読み、`cache_protocol_analysis` で構造化結果を保存します。`next_offset` がある間は `analyze_protocol(offset=next_offset)` を繰り返します。保存済みの場合は `analyze_protocol` が即座に解析結果を返します。

**内部LLM**: `LLM_BACKEND=compatible`では`LLM_BASE_URL`（`/v1`等まで）、`LLM_MODEL`、必要なら`LLM_API_KEY`を設定し、Chat Completions互換APIを呼びます。`LLM_BACKEND=litellm`ならGemini・Anthropic・OpenAI・Azure OpenAI・Amazon Bedrockへ接続できます。[設定例と追加探索](docs/exploration.md)を参照してください。compatible方式は`response_format: json_object`対応モデルが必要です。LiteLLM方式ではJSON生成を指示し、返却後にスキーマを検証します。関連セクションを45,000文字以下のバッチに分割して抽出し、検証後に保存します。プロトコルの関連テキストは設定先プロバイダに送信されます。外部APIはキー未設定のため今回の実API検証は未実施です。

保存対象は、研究デザイン、対象集団、曝露、比較群、アウトカム、統計解析、疾患定義、データソース、補足事項です。疾患定義には、記載されていればコード体系・コード・観察期間・判定アルゴリズムを残します。OMOPへの自動変換や記載のないコードの補完は行いません。

各事実に短い原文引用・PDFの物理ページ番号・セクションを付けます。引用の存在、ページ、指定されたセクションをコードで検証します。データソース名は引用中に存在する名前に限定します。研究・PDF URL、版、取得日時、SHA256、抽出方式、schema versionは共通の `source` に保持します。引用が存在することは、要約や使用区分の意味が正しいことの保証ではありません。

## 検索結果の必須データソース情報

全検索結果に次のキーが含まれます。

| キー | 内容 |
|---|---|
| `data_source_types` | カタログのData source type。取得できない場合は空配列 |
| `data_source_types_status` | `available` / `not_provided` |
| `catalogue_data_sources` | カタログ上の名称。プロトコル本文由来と混同しないため別欄 |
| `protocol_data_sources` | プロトコル本文由来の名称・使用区分・引用・ページ |
| `protocol_data_sources_status` | `extracted` / `not_analyzed` |
| `protocol_source` | 名称を抽出したPDF URL・版・取得日等 |

**未解析のPDFの名称は推測して埋めません。** 初回は `not_analyzed` と空配列を返します。候補を解析・保存してから再検索すると必ず同じ結果形式に本文由来の名称が付与されます。常に本文の抽出済み結果だけを必要とする場合は `analyzed_only=true` を指定します。解析済みでも本文に名称が明示されない場合は空配列になり、解析本体の `missing_information` で理由を確認します。

プロトコルは計画書なので、`usage` を `used` / `planned` / `candidate` / `unclear` に区別します。計画書に使用予定と書かれたDBを、実際に使用したと断定しません。

## MCP接続

stdioで13個のToolを公開します。初期抽出の5個、[追加探索の7個](docs/exploration.md)、公式医薬品辞書を更新する`refresh_drug_dictionary`です。

| Tool | 主な引数・動作 |
|---|---|
| `search_studies` | `query`, `limit=5`（最大20）, `darwin_only=true`, `status`, `analyzed_only=false`。通信なし |
| `get_study` | `study_id`, `refresh=false`。研究種別とData source typeを各タブで確認 |
| `get_protocol` | `study_id`, `version="latest"`, `download=true`, `refresh=false` |
| `analyze_protocol` | `study_id`, `force_refresh=false`, `offset=0`, `max_chars=30000` |
| `cache_protocol_analysis` | `study_id`, `fingerprint`, `analysis`, `coverage_complete=true` |

Claude Code等のJSON形式MCP設定例:

```json
{
  "mcpServers": {
    "ema-rwe": {
      "command": "E:/codex/rwd-catalogue-mcp/.venv/Scripts/ema-rwe-mcp.exe",
      "args": [],
      "env": {
        "EMA_DB_PATH": "E:/codex/rwd-catalogue-mcp/data/ema.sqlite3",
        "EMA_CACHE_DIR": "E:/codex/rwd-catalogue-mcp/cache/http"
      }
    }
  }
}
```

Codex CLIの登録例（`codex mcp add --help` で引数確認済み）:

```powershell
codex mcp add ema-rwe --env EMA_DB_PATH=E:/codex/rwd-catalogue-mcp/data/ema.sqlite3 --env EMA_CACHE_DIR=E:/codex/rwd-catalogue-mcp/cache/http -- E:/codex/rwd-catalogue-mcp/.venv/Scripts/ema-rwe-mcp.exe
```

依頼例:

> EMA RWEでopioid cohortを検索し、上位2件の最新版プロトコルを解析してください。needs_client_extractionなら全バッチを読んでcache_protocol_analysisに保存し、再検索結果にData source type、本文のデータソース名、疾患定義、研究デザインと出典を付けて比較してください。

日英語・略語・英米綴りの同義語を展開し、`query_expansion`に展開内容を返します。網羅的な医学辞書ではありません。呼出元が`synonyms`を追加でき、`plan_study_search(use_llm=true)`では内部LLMに複数の検索式を作らせられます。研究デザイン・疾患定義・データソースの構造化抽出もFTSに追加されます。BM25値は関連度比較用で、確率ではありません。

## 最新版とキャッシュ

- Study IDとDrupalのnode IDは別物です。Studyページの実リンクから各タブへ移動します。
- Updated protocol > Protocol > Initial protocol。同じ分類内は、全候補に版があれば数値版番号、なければ全候補の文書日付、最後に利用可能な公開日で選択します。タイトル中の版・日付を用い、ファイルのアップロード日時だけでは比較しません。不十分な場合は `selection_reason` に不確実性を示します。PDF本文の全版を読み比べた意味的な最新版判定は行いません。
- `get_protocol` は候補一覧も返します。ファイル名の版が不正確な場合は一覧と本文を確認してください。
- HTTPのHTML/PDFレスポンスキャッシュは既定7日間です。取得したプロトコルPDFは、追加要望に従って別途`EMA_PROTOCOL_DIR`（既定はDBと同じ親フォルダ内の`protocols`）へID付きで保存し、ユーザーが削除するまで保持します。全文テキストは恒久索引化しません。起動時・期限切れ再取得時・`cleanup-cache` で期限切れを削除します。停止中に時刻通り削除する常駐ジョブは作りません。抽出全文はDBに保存しません。
- 構造化結果は永続保存します。URL、版、PDFのSHA256、schema、parser、prompt、モデル・provider設定からfingerprintを生成します。
- 解析時はDocuments/PDFをTTLに従い再検証し、同一URLの差し替えも検出します。通常検索はネット通信しないので、最新版とは `protocol_source` に記録した確認時点のものです。即時確認には `force_refresh=true` を使います。
- 1プロセス内のHTTP同時数1、最小2秒間隔、タイムアウト30秒、最大3回リトライです。Retry-Afterを優先し、60秒を超える待機指定は再試行可能時刻の目安付きエラーとして返します。
- robots.txtを確認し、取得先／リダイレクト先はEMA CatalogueのHTTPSに限定します。外部ホストのプロトコルは取得しません。
- PDFは50 MiB／1,500ページ／抽出500万文字まで。OCR・暗号化PDFは対象外です。

```powershell
.venv/Scripts/ema-rwe analyze 1000000479 --refresh
.venv/Scripts/ema-rwe cleanup-cache
```

## 検証

```powershell
.venv/Scripts/pytest -q
.venv/Scripts/ruff check src tests
```

通常テストはネット通信せず、固定HTML断片、合成PDF、MockTransportを利用します。CSV取込・検索・対象外排除・新版選択・PDF抽出・引用検証・解析保存・再検索・差し替え検出・HTTPリトライ・期限切れ削除・MCP stdio初期化／discovery／実行／入力エラーを確認します。

依存関係は `uv.lock` に固定しています。uv以外では `pip install -e ".[dev]"` でもインストールできます。

実サイト検証と制限は [docs/validation.md](docs/validation.md) を参照してください。仕様の10〜20プロトコルによる人手精度評価は未実施であり、研究設計に転用する前に出典を確認してください。
