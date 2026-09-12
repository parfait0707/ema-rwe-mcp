# 同義語・章構造・ローカルPDF追加探索（v0.2）

## 検索語の十分性

`search_studies` は元のqueryに加え、`vocabulary.py` の概念グループから同義語を展開する。例: 出血 → bleeding / haemorrhage / hemorrhage、new-user → incident user、欠測 → missing data / missingness / imputation。英語は単語境界を判定し、複数語の同義語はFTSでもフレーズとして保つ。例えばNOACの展開を単語分割して `non` だけで全研究をヒットさせない。

薬剤クラスと個別薬、IPTWと傾向スコア全体などは同一概念とみなさない。曖昧なAF/PSは自動展開しない。辞書は網羅的な医学用語集ではなく、実際に用いた展開を `query_expansion` で確認できる。呼出元は `synonyms=[...]` を追加できる。

`plan_study_search(question, use_llm=true)` は内部LLMに疾患・曝露・アウトカム・designを分けた最大5つの検索式と追加同義語を作らせる。`use_llm=false` はネット通信なしで辞書展開を返す。**このToolは検索計画を返すだけ。** 各queryを `search_studies` で確認し、不足した概念や過剰に広がった概念を修正する。全検索語を `compare_protocols` に渡して候補を統合・重複除去し、一次判定上限（既定5件）を超えたら追加条件をユーザーに確認し、上限以内なら全件を処理する。source_assessmentsでデータタイプと定義用途を根拠付きで保存する。[用途別分類](source-types.md)を参照。[比較ワークフロー](comparisons.md)を参照。取りこぼしがないことを保証するものではない。

## 章構造

PDFをページ付きの章チャンクへ分け、各チャンクに以下を付与する。

- `section_id`: 同じPDFとparser版で再現可能なチャンクID。ひとつの章が複数ページ・チャンクに分かれる場合がある。
- `section`: 見出し、`parent_section`: 親章、`previous_section` / `next_section`: 前後の見出し。
- `role` / `context_role`: methods、data_sources、analysis、definitions、references、checklist等。
- `structure_warnings`: 章番号の逆行など、構造の確認が必要な箇所。

番号付き見出し、語義グループ、親章の文脈、付録への移行を合わせて判断する。固定の「8章なら必ずmethods」という条件にはしない。章番号が異なるテンプレートも扱う。目次、背景、連絡先などの管理章、参考文献、ENCePPチェックリストは通常の構造化抽出から外す。背景・参考文献にanalysisと書かれていても、それだけで当該研究の解析計画とは扱わない。

ENCePPチェックリストは独自の1～12の章番号を持つ。付録境界をページ間で引き継ぎ、「欠測値の処理は記載されているか？」という設問を欠測値処理方法として抽出しないよう分離する。追加探索ではこれらの章も閲覧可能だが、チェックリストの設問そのものを方法の根拠にはできない。

章分類はヒューリスティックであり、PDFごとの構造を完全に解釈するものではない。LLMにもrole・親章・前後関係の確認と矛盾の報告を指示する。引用照合が通っても解釈の正しさや完全性は保証しない。

共通抽出の研究方法・データソース欄については、引用が背景・参考文献・チェックリスト・管理章にしか存在しない場合、保存時にも `EVIDENCE_WRONG_SECTION` で拒否する。補足事項や質問別回答では、その章自体についての質問もあるため引用を許容する。unknownと分類された章については意味的な妥当性の確認が引き続き必要。

## PDFの安定IDと保持

`get_protocol(download=true)` または `analyze_protocol` で取得したPDFは以下の形式で保存する。

```text
<EMA_PROTOCOL_DIR>/pdf_<Study ID>_<PDF全文のSHA256>.pdf
<EMA_PROTOCOL_DIR>/pdf_<Study ID>_<PDF全文のSHA256>.json
```

`EMA_PROTOCOL_DIR` の既定値はDBと同じ親フォルダ内の `protocols`。`get_protocol` の `protocol` と解析結果の `source` に `protocol_id` / `local_filename` を返す。検索結果にも `local_protocols` 一覧を付け、解析済みなら `protocol_id` を付ける。同じPDF内容は同じIDになり、同じURLでファイルが差し替わると別IDになる。旧版も保持する。保存済みIDでの探索は最新版の確認とは別であり、最新版を確認したい場合は `get_protocol(refresh=true)` を使う。

PDFは追加要望に従ってユーザーが削除するまで保持する。既存のHTTPキャッシュとは別なので、`cleanup-cache` では削除されない。削除する場合は対象IDの `.pdf` と `.json` を削除する。これ以降そのIDの探索は明示的なmissingエラーを返す。全文テキストは都度メモリへ抽出し、永久保存・全文DB化しない。

## 呼出元LLMによる追加探索（APIキー不要）

| Tool | 機能 |
|---|---|
| `plan_study_search` | 同義語の確認／内部LLMによる検索式の作成 |
| `list_local_protocols(study_id)` | ローカル保存した各版とID一覧 |
| `get_protocol_outline(protocol_id, offset=0, limit=100)` | 全文の章一覧。最大200チャンク／回 |
| `search_protocol_text(protocol_id, query, synonyms=[], limit=10)` | 初回除外した章も含むPDF全文検索。最大30件 |
| `read_protocol_text(protocol_id, section_id=..., offset=0)` | チャンク全文を読む |
| `research_protocol(protocol_id, question, force=false)` | 保存回答を再利用、または問い合わせ別の探索を開始 |
| `cache_protocol_answer(protocol_id, question, answer)` | 問い合わせ別の出典付き回答を検証・保存 |

`read_protocol_text` は `section_id` の代わりに `start_page` / `end_page` で物理PDFの1～5ページを指定できる。最大20,000文字／回で、`next_offset` があるときは同じ対象と次のoffsetで続きを読む。

内部LLM未設定の `research_protocol` は `needs_client_exploration`、初期検索ヒット、回答schemaを返す。呼出元LLMは追加語で検索し、章一覧や前後ページを辿ってから保存する。回答schemaは `answers: [{value, evidence:[{page,section,quote}]}]` と `missing_information`。元の共通抽出schemaを上書きしない。質問別回答は別SQLiteテーブルに保存する。

例:

```text
get_protocol(study_id="1000000479")
# 戻り値の protocol.protocol_id を以降の引数に使う
research_protocol(protocol_id="pdf_...", question="欠測値処理はどう計画されていますか？")
search_protocol_text(protocol_id="pdf_...", query="missing data", synonyms=["complete case", "multiple imputation"])
get_protocol_outline(protocol_id="pdf_...")
read_protocol_text(protocol_id="pdf_...", start_page=28, end_page=31)
cache_protocol_answer(protocol_id="pdf_...", question="欠測値処理はどう計画されていますか？", answer={...})
```

ヒットなしだけを理由に「記載なし」と断定しない。章・本文を確認しても不明なら `missing_information` に調査範囲と不明点を書く。

CLIも共通Coreを使用する:

```powershell
.venv/Scripts/ema-rwe plan-search "高齢者の出血リスク" --llm
.venv/Scripts/ema-rwe local-protocols 1000000479
.venv/Scripts/ema-rwe outline "pdf_..."
.venv/Scripts/ema-rwe pdf-search "pdf_..." "欠測値処理" --synonym "complete case"
.venv/Scripts/ema-rwe pdf-read "pdf_..." --start-page 28 --end-page 31
.venv/Scripts/ema-rwe ask "pdf_..." "欠測値処理はどう計画されていますか？"
```

## LiteLLM経由の内部探索

```powershell
uv sync --extra dev --extra llm
```

`LLM_BACKEND=litellm` と `LLM_MODEL=<provider>/<model-id>` を設定する。内部の `research_protocol` はJSONで search / outline / read / finish を選び、観測結果を見て次の行動を決める。外部ページ・シェル・任意パスにはアクセスせず、指定IDのローカルPDFだけを探索する。通常の共通抽出も同じLiteLLM接続を使うが、共通抽出自体は関連章のバッチ処理。

`LLM_MAX_STEPS` は既定8、上限20。各API呼出しはタイムアウト180秒・出力上限6,000トークン、APIリトライ0回。上限に到達した場合は `exploration_limit_reached` と探索履歴を返し、未完了の回答は保存しない。回答の引用・ページ・セクションを検証してから保存し、同じPDF ID・質問・モデル／backend／parser版では再利用する。

MCP設定の `env` に入れる共通設定例（モデルIDは利用可能なものに置き換える）:

```json
{
  "LLM_BACKEND": "litellm",
  "LLM_MODEL": "gemini/<model-id>",
  "LLM_API_KEY": "<your-key>",
  "LLM_MAX_STEPS": "8",
  "EMA_PROTOCOL_DIR": "E:/codex/rwd-catalogue-mcp/data/protocols"
}
```

これはMCPサーバー設定の `env` オブジェクトの例。READMEの `mcpServers.ema-rwe.env` に設定する。APIキーはリポジトリに保存せず、利用環境の設定に入れる。外部LLMには質問と選択したPDF本文が送信される。

| 接続先 | `LLM_MODEL` | 認証・追加設定 |
|---|---|---|
| Gemini | `gemini/<model-id>` | `LLM_API_KEY` または `GEMINI_API_KEY` |
| Anthropic | `anthropic/<model-id>` | `LLM_API_KEY` または `ANTHROPIC_API_KEY` |
| OpenAI | `openai/<model-id>` | `LLM_API_KEY` または `OPENAI_API_KEY` |
| Azure OpenAI | `azure/<deployment-name>` | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_API_VERSION`。または `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION` |
| Amazon Bedrock | `bedrock/<model-or-inference-profile-id>` | AWS認証チェーン（プロファイル、ロール、アクセスキー等）と `AWS_REGION_NAME`。IAM認証では `LLM_API_KEY` を設定しない |

LiteLLMの接続仕様は [基本ドキュメント](https://docs.litellm.ai/)、[Gemini](https://docs.litellm.ai/docs/providers/gemini)、[Azure](https://docs.litellm.ai/docs/providers/azure)、[Bedrock](https://docs.litellm.ai/docs/providers/bedrock) を参照。Azureはモデル名ではなくデプロイ名を指定する。LLM_MODELに使うモデル／リージョン／API版の利用可否は契約先による。

## 検証範囲

5プロバイダ名の引数ルーティング、LiteLLM実SDKのmock completion、検索→読取り→回答保存の探索ループ、上限停止、引用不一致拒否、プロセス再作成後のローカル再利用をオフラインテストする。各プロバイダの実API認証・実モデルの抽出精度は未検証。
