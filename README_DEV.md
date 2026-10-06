# EMA RWE MCP — 開発者向け

エンドユーザー（このMCPを使って研究を調べる人）向けの使い方は[README.md](README.md)を参照してください。本ファイルはこのMCPサーバー自体を開発・改修する人向けの内部仕様です。

公開版は0.5.3（内部履歴では0.1〜1.6の仕様段階を経ている）。`docs/spec/v0.1.md`〜`v1.6.md`が仕様の正本、[docs/mcp-workflow.md](docs/mcp-workflow.md)が呼出元向け手順の正本、開発ルールは[AGENTS.md](AGENTS.md)です。

## 開発用セットアップ（checkout）

Python 3.12以上とuvを使います。

```powershell
uv venv --python 3.12
uv sync --extra dev
$env:EMA_DB_PATH = "<checkout>/data/ema.sqlite3"
$env:EMA_CACHE_DIR = "<checkout>/cache/http"
$env:EMA_IMPORT_DIR = "<checkout>/data/imports"
.venv/Scripts/ema-rwe --help
```

内部LLM経路も試す場合は`uv sync --extra dev --extra llm`とします。別の場所にcloneした場合はパスを置き換えてください。Windows以外では`.venv/bin/ema-rwe`を使用します。

`EMA_DB_PATH`未設定時は、`config.py`の`default_data_dir()`がチェックアウト内（`pyproject.toml`がある場所）かどうかを判定し、チェックアウト内なら`<repo>/data/ema.sqlite3`（Git管理のカタログDB）を、そうでなければOSのユーザーデータディレクトリ（初回のみ同梱データを複製）を使います。キャッシュは`EMA_CACHE_DIR`未設定時にOSのユーザーキャッシュディレクトリが既定です。`.env.example`は設定例のドキュメントであり、**このリポジトリは`.env`を自動読込しません**（`python-dotenv`等は使用していません）。環境変数はシェルまたはMCPクライアントの`env`で渡してください。

## レイアウト

`src/ema_rwe/`配下、Core・CLI・MCPアダプタを分離しています。

| モジュール | 役割 |
|---|---|
| `service.py` | Core service。`Service`クラスが全MCPツール／CLIコマンドの実処理を持つ |
| `mcp/server.py` | MCPアダプタ。`FastMCP`で17ツールを公開し、Pydanticスキーマの`title`を除去して応答量を削減する |
| `cli.py` | CLIエントリポイント（`ema-rwe`コマンド）。Coreと同じServiceを呼ぶ |
| `storage.py` | SQLite永続化（`Repository`、DBスキーマ6）、FTS5マッチ式生成（`fts_match`）、CSV取込（`import_csv`）。PDF取得で分かった事実（プロトコルの有無・テキスト層・補完した医薬品と出所ごとの抽出規則の版`exposure_rules`）は`protocol_observations`表に分けて保存し（`observe`）、補完した医薬品の名前とATCコードを索引の医薬品の列に入れる。同梱DBの観測の統合（`merge_bundle_observations`、`_merge_observations`）は項目単位で手元を優先し、医薬品だけは出所ごとに、同梱DBの方が新しい規則の版ならその出所の値を置き換える（`_with_newer_exposures`） |
| `archive.py` | ユーザー要求で保持する不変ID付きPDFと、英語以外のPDFの見出しの英訳（期限切れ削除の対象になるHTTPキャッシュとは別） |
| `pdf.py` | ページ単位のネイティブPDF抽出。引用検証（`validate_evidence`）、未検証証拠の除去（`prune_unverifiable`） |
| `llm.py` | OpenAI互換/LiteLLM経由のJSON補完呼出し、バッチ分割（`split_batches`）、複数バッチの統合（`merge_extractions`） |
| `exploration.py` | `Explorer`。追加探索（`research_protocol`）の全文一括回答とステップ制限探索ループ |
| `comparison.py` | 永続的な全候補比較エクスポート。未完了研究を隠さない |
| `terminology.py` | 利用者の概念辞書（`data/dictionaries/*.json`または`EMA_TERMINOLOGY_PATH`、既定ではなし）の読み込み、語の出所（`term_sources`）、コード表記の展開、FTS用の語群と類縁語群。プロトコル由来の定義とは別概念として保持 |
| `drugs.py` | 公式EMA医薬品（商品名/INN・common name/ATC）の対応表。オフラインキャッシュ。照合は名前（製品名・成分の組み合わせ）だけで行い、ATCコードでは引かない。`whole_term=True`は検索語を1つの医薬品名として語全体で照合し、合わせ剤は成分の組み合わせ全体が一致する製品にだけ解決する |
| `medicines.py` | カタログのexposures欄の「(ATCコード) 名称」とEMA医薬品辞書から、成分の上位クラス（カテゴリー語）とクラスの所属薬（検索語）を名前で展開する（`expand_medicine`、応答の`medicine_expansion`）。コードの名称はカタログの記載から引き、EMA辞書のコードはクラスの所属薬を名前で集めるときだけ使う。補完の抽出規則もここにある：文章の既知の医薬品名（`find_medicines`。研究の略称・測定される物質を除く）、PASS情報表の医薬品欄の名前・カタログのクラス名・ATCコード（`pass_table_medicines`、`known_class_names`。コードは実在する第2レベルの群だけ、第2レベルは「ATC」の語か名称があるときだけ、隣のコードを越えて名前に付けない）。規則を変えたら`EXPOSURE_RULES`の出所の版を上げる |
| `vocabulary.py` | 研究デザイン・手法・集団の語だけの小さな展開表。臨床語は持たない（翻訳は呼出元か利用者辞書） |
| `selection.py` | 検索フィルタの明示的な定義と、切り詰めのない候補集計 |
| `ranking.py` | 一次検索の概念ブロック（`ScreeningBlock`）、全列での取得、候補の順位付け（公開プロトコルなし・テキスト層なしを最後に、固有語・役割の列・CSVのプロトコル所在・研究タイプ・統合順位。候補は削らない） |
| `config.py` | `Settings`（環境変数の正本）。既定パスの解決とwheel同梱データの初回複製 |
| `domain.py` | 共通の例外型（`RWEError`）とドメインモデル |
| `ema.py` | EMAカタログHTML（研究ページ・Study documents）のパース、Non-interventionalの判定、最新プロトコルの選択（`select_protocol`）。I/Oなし、表記揺れに耐性。CSVの解析は`storage.import_csv` |
| `http.py` | `EMAClient`。robots確認、間隔制御、リトライ付きHTTP取得 |
| `source_types.py` | PDF由来データタイプ判定の表示（`assess_row`）と、判定状態による並べ替えと比較表の行選択（`select_rows`） |

## テスト・lint・ビルド

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv build
```

通常テストはネット通信せず、固定HTML断片・合成PDF・`MockTransport`を使用します。CSV取込・検索・対象外排除・新版選択・PDF抽出・引用検証・解析保存・再検索・差し替え検出・HTTPリトライ・期限切れ削除に加え、**MCP stdio初期化／discovery／実行／入力エラーの実stdio MCPテスト**を含みます。ツールのスキーマを変更した場合は必ずこれらを実行してください。依存関係は`uv.lock`に固定しています。uv以外では`pip install -e ".[dev]"`でもインストールできます。

## 配布

`pyproject.toml`の`[tool.hatch.build.targets.wheel.force-include]`で`data/ema.sqlite3`・`data/ema-medicines.json`をwheel内の`ema_rwe/data/`へ、呼出元の手順書`docs/mcp-workflow.md`を`ema_rwe/docs/`へ同梱しています（手順書はMCPリソース`ema-rwe://docs/mcp-workflow`として返し、チェックアウトでは`docs/`の原本を読む。`config.workflow_doc_path`）。`config.py`の`default_data_dir()`は、チェックアウト外（`pyproject.toml`が見つからない環境、つまりwheelインストール後）で起動された初回だけ、これらの同梱ファイルをOSのユーザーデータディレクトリ（`platformdirs.user_data_path("ema-rwe-mcp")`）へコピーします。2回目以降はユーザーデータディレクトリの既存ファイルを使います。`EMA_DB_PATH`で別のDBを指定した場合、そのDBは差し替えも統合もしません。ただしカタログDBは、同梱DBの最新取込日時（`imports.imported_at`の最大値）が手元のDBより新しいときだけ、カタログの表（`studies`・`imports`・`study_catalogue_search`・`study_fts`）だけを同梱DBの内容に置き換え、同梱DBの`protocol_observations`（補完の結果）で手元の観測に無い項目を研究ごとに補います（カタログの取込日が同じでも補います。手元の項目が優先）。ただし補完した医薬品（`exposures`）は出所（`catalogue_text`・`protocol_pass_table`）ごとに抽出規則の版（`exposure_rules`、`medicines.EXPOSURE_RULES`）を持ち、同梱DBの方が新しい版で抽出した出所は、手元のその出所の値を同梱DBの値で置き換えます。抽出規則を直した版を配ると、既存の利用者にも修正が届きます。置き換えは手元のDBの中で1回の書き込みトランザクションとして行うので、動作中の別サーバーからは新旧どちらかのカタログが見え、解析キャッシュ（`analyses`・`analysis_history`・`protocol_answers`）には触れません（`storage.refresh_from_bundle`、1プロセスにつき1回。失敗しても起動は止めず、元のカタログのまま使います）。同梱DBのスキーマ版が異なる場合は置き換えません。利用者が自分でCSVを取り込んで同梱DBより新しくなっている場合は置き換えません。`get_study`で再取得した研究の行は同梱DBの内容に戻ります。`get_protocol`が記録した事実は`protocol_observations`にあるので失われません。医薬品辞書は上書きしません（`refresh_drug_dictionary`で更新します）。

`.mcp.json`（このリポジトリ直下、Git管理外）は各自の登録で、開発中は`uv run --directory /path/to/repo ema-rwe-mcp`によりチェックアウトを直接起動できます（作業ツリーの未コミット変更もそのまま反映されます）。公開版を確かめるときは`.mcp.json.sample`と同じ`uvx`の形にします。`.mcp.json.sample`（Git管理対象）はエンドユーザー向けで、`uvx --from git+https://...`によりcloneなしでリモートのコードを取得・起動します。両者は起動対象（ローカル作業ツリー vs. リモートのgit ref）が異なる点に注意してください。

リポジトリを公開する場合は次を行います。

1. `.gitignore`を再確認する（現状は`data/`配下のうちカタログDB・医薬品辞書・辞書の記入例・関連語の判定記録の4ファイルと`imports/`の空フォルダだけを追跡し、他はすべて除外。PDF・DB・キャッシュ・`.env`・`docs/agent_brief/`・`docs/agent_report/`も除外済み）。
2. リリースタグ（`v<pyproject.tomlの版>`）を打つ。手順は[docs/release.md](docs/release.md)。

PyPI公開やGitHub Releaseへのwheel添付など他の配布経路の比較検討は[docs/research/202609160750_mcp_distribution.md](docs/research/202609160750_mcp_distribution.md)を参照してください。

## カタログ取込の内部仕様

`import_catalogue_csv(filename)`は`EMA_IMPORT_DIR`（既定は`EMA_DB_PATH`と同じ`data/`配下の`imports/`）の`studies/`または`source_type/`直下のファイル名のみを受け付けます（移行用にルート直下も読みます）。任意パスは受け付けません。

- **必須列**: `Study ID`、`Official title and acronym`（公式CSVでは`Title`）、`Study type`。別名の対応は`storage.py`の`ALIASES`を参照してください。不一致はエラーになり、黙って0件登録しません。列名が異なる場合はCLIでは`--column-map ./columns.json`、MCPの`import_catalogue_csv`では`column_map`（辞書）を使います（例: `{"study_id":"Study identifier","title":"Title","study_type":"Type"}`）。
- **Non-interventional判定**: `Non-interventional study` / `Non-interventional`と明示された研究だけ取り込みます。種別不明・介入研究は除外します。URL中の`content_type:darwin_study`は研究レコード種別で、DARWIN EU研究限定とは別です（`darwin_only`は独立したフラグで判定）。
- **種別タグ（`data_source_types`）**: `Data sources (types)` / `Data source type`列があれば直接取り込みます。ただし2026-09-12/13時点のStudies exportにはこの列がないため、EMA検索画面でData source typeをclaims/EHR/registryに限定してexportしたCSVを`source_type/`へ置き、**ファイル名に埋め込んだ種別だけをタグの根拠**とします。ファイル名は`<日付>_<claims|ehr|registry>_export-data.csv`とし、種別トークンが1つだけ含まれる必要があります。種別exportを取り込む前の研究はすべて`others`として扱われます。`source_type/`のファイルは各研究の`data_source_types`に種別を追加し（複数種別は累積）、その後に全件exportを再取込してもタグは保持されます。
- **区切り**: 複数値の区切りは`|`・`;`・改行です。値内部のカンマは分割しません。
- **プロトコル所在（`protocol_listed`）**: `Protocol file(s)`・`Protocol file(s) - URI`・`Protocol URL`のいずれかに値があれば`true`、すべて空なら`false`、列が無ければ未設定です。順位付けにだけ使い、最新版の選択には使いません（最新版はStudy documentsで選びます）。
- **再構築**: `data/imports/{studies,source_type}/`にexportを置いて`uv run ema-rwe import-all`を実行すると、`studies/`、`source_type/`の順にすべて取り込み、最後に`VACUUM`でDBファイルの空き領域を詰め直します（`merge-observations`も同じ。同梱DBは、この2つのコマンドで作ります）。
- **医薬品欄の補完**: `uv run ema-rwe backfill-protocols [--interval 60] [--limit N] [--no-download] [--reextract]`。医薬品欄が空の研究に、題名・説明・目的の既知の医薬品名（通信なし）と、CSVにプロトコルの所在がある研究のプロトコルのPASS情報表（Active substance・Medicinal product）の既知の医薬品名・カタログのクラス名・欄に書かれたATCコード・ページを補います（spec v1.2、v1.3）。研究の略称と同じ名前、検査値として書かれた物質名、直後にreceptor・inhibitorなどが続く名前は補いません。1件ずつ、研究の間を`--interval`秒空け、429や通信障害で止まり（HTTP層の再試行の後）、読み終えた研究（`backfill_done`）は飛ばして再開します。同梱DBを作るときは、作業用のDB（`EMA_DB_PATH`）で実行してから、`EMA_DB_PATH`を外して（チェックアウトの`data/ema.sqlite3`を対象にして）`uv run ema-rwe merge-observations <作業用DB>`で同梱DBに観測を加えます（配る項目だけを移し、索引も作り直します。失敗はエラーとして返します）。抽出の規則を直したときは、作業用DBで`--reextract --no-download`を実行して保存済みのPDFから読み直し、`medicines.EXPOSURE_RULES`の該当する出所の版を上げます（同梱DBの新しい版の結果が、利用者のDBの古い結果を置き換えます）。
- **upsert**: 研究ID単位のupsertです。今回のCSVにない既存研究は削除しません。元CSVのバイト列、SHA256、ファイル名、取込時刻を保存します。
- **連絡先の非索引化**: 原本CSVに連絡先が含まれる場合があります。原本は検索対象から分離され、連絡先専用列はDB／FTS／検索結果には入れません。
- **Data Sources CSVは不要**: カタログの種別タグはローカル候補の絞り込みだけに使い、定義ごとのデータタイプは候補PDFの該当用途からLLMで判定して公式のStudy分類と分けて保存します（[docs/source-types.md](docs/source-types.md)）。
- **Playwright MCP経路の理由**: `/search/`配下はrobots.txtで明示的にDisallowされているため、このMCP自身は検索結果ページやExport URLをHTTPでクロールしません。代わりに、Microsoft公式[Playwright MCP](https://github.com/microsoft/playwright-mcp)を呼出元用の別MCPとして併用し、ユーザーが開始した調査で公式の画面手順（`Export Results`）を1回だけ実行します。ブラウザをPythonサーバーへ埋め込まず画面操作を呼出元へ分離することで、DOM変更時にLLMが要素を再探索でき、検索・検証・保存処理も単独で利用できる設計です。`EMA_CATALOGUE_TTL_SECONDS`（既定30日）以内で候補が0件の場合は先に英訳・同義語・コード・絞込条件を見直し、同じsnapshotの再取得を避けます。

## 抽出と検証の仕組み

**読む範囲の決め方（`pdf.py`）**: PDFにしおり（ブックマーク）があれば、それを一次の目次として使います。各ページに「そのページを含む最も深いしおり」と「その最上位の章」を付け、本文の見出し検出で役割が決まらない章にはしおりの題名から役割を与えます（`apply_bookmarks`）。抽出で読む順序は`reading_order`が決め、しおりがある場合は本文の章を先に、付録は本文が参照しているもの（「Annex 3」「Appendix V」）とコードリスト・変数定義の題名を持つものだけを読みます（履歴書・ENCePPチェックリスト等は読みません）。しおりのないPDFでは、印字ページのずれを確かめた目次で節の始まりを決め、目次も無ければ本文より大きいか太字で章番号が続く行を章見出しとし、関連セクションをすべて読みます（spec v1.5）。節の役割は、読むか、引用を受理するか、抽出の指示の三つに別々に使います。質問別探索（`search_sections`）はしおりの章名に一致した語を最優先で採点します。実測では242頁のプロトコル（19786）の読取量が45.6万字から10.4万字に減り、他のPDFは付録参照の有無に応じてほぼ不変です。

**英語以外のプロトコル（spec v1.6）**: 役割の語は英語なので、本文が英語でない文書（英語の機能語が全語の8%未満）は、見出しとしおりの題名（`pdf.heading_texts`）の英訳から役割を判定します。`analyze_protocol`はPDFごとに一度だけ言語を判定し（`service._check_heading_language`）、内部LLMがあれば`llm.translate_headings`で英訳し、無ければ`status=needs_heading_translation`と見出しの一覧を返します。呼出元は`cache_heading_translations`で英訳を保存します（`{}`なら英訳なしで読む）。判定結果と英訳は保存したPDFの隣の`<protocol_id>.headings`にparserの版とともに置き（`archive.headings`）、`extract_pages(translations=...)`が各`Page.heading_translations`に渡すので、`sections`を呼ぶ全経路（抽出・引用検証・探索ツール）が同じ役割を使います。英訳は役割の分かる節を外すためだけに使い、本文の手がかり語（英語）が英語でない本文を読めないので、役割の分からない節は読みます。英訳はfingerprintに入れません（比較表が翻訳前にfingerprintを記録するため）。英訳を別の内容で保存し直すと、そのPDFの保存済み解析と途中のバッチを破棄します。

**抽出スキーマ v0.3（`domain.py`）**: `cohort`ブロックを追加しました。`inclusion_criteria[]`・`exclusion_criteria[]`（各基準を1事実として根拠付き）、`index_date`、`baseline_period`（連続加入・ルックバック）、`follow_up`（開始・終了・打ち切り）、`design_schema`（設計図の物理ページ`figure_pages`と、本文にある対応する時間窓の記述`time_windows[]`）です。図そのものは画像のため読みません。fingerprintに`schema: 0.3`が入るため、既存の保存済み抽出は次回`analyze_protocol`で再抽出されます。比較表には「コホート定義」「設計図（ページ・時間窓）」の行が加わります。

`analyze_protocol`は2つの経路を持ちます。

**呼出元抽出**（`LLM_MODEL`が未設定、または`LLM_BACKEND=compatible`で`LLM_BASE_URL`が未設定のとき）: 英語以外のプロトコルで英訳が無ければ、先に`needs_heading_translation`を返します。それ以外は`needs_client_extraction`と関連セクション・物理PDFページ・抽出JSON Schema・fingerprintを返します。呼出元は`offset`を進めながら`analyze_protocol(offset=next_offset)`で全バッチを読み、バッチごとに`cache_protocol_analysis(batch_offset=offset)`で途中保存します（サーバーは保存済みoffsetを`cached_batch_offsets`で返すので、コンテキスト圧縮後も既読分を再読しません。途中保存はサーバープロセスのメモリにあるだけで、サーバーを再起動すると失われます。`status=extracting`の進行中の抽出も同じです）。最後のバッチで`coverage_complete=true`を渡すとサーバーが全バッチを統合して保存します。

**サーバー側抽出**（`LLM_MODEL`があり、かつ`LLM_BACKEND=litellm`または`LLM_BASE_URL`があるとき）: 関連セクションを`LLM_BATCH_CHARS`（既定300,000文字）以下のバッチに分割し、`LLM_CONCURRENCY`（既定4）並列でプロバイダに投げて統合します（`llm.py`の`split_batches`/`merge_extractions`）。抽出が`LLM_WAIT_SECONDS`（既定120秒）を超えるときは`status=extracting`を返すので、呼出元は同じ`analyze_protocol`をポーリングします。保存前に`service.py`が`pdf.finalize_extraction`を呼び、その中の`prune_unverifiable`が証拠を検証します。隣のページに引用があればページを直し、データソース名が引用に無ければ名前を含む引用を加え、それでも原文と一致しない証拠だけを落として、件数と監査メモを`missing_information`に記録します。プロンプトはLLMに`evidence.section`を`null`のまま返すよう指示し（`llm.py`）、サーバーが引用の位置から節ラベルを導出します（`pdf.py`の`with_section`）。抽出プロンプトは節ラベル省略・電報体の`value`/`definition`・JSON最小化を指示しつつ「事実・コード・条件の省略禁止」も明示しており、出力トークンを削減しながら証拠件数を落とさない設計です（実測は[docs/validation.md](docs/validation.md)の2026-09-24節を参照）。

`research_protocol`（内部LLM設定時）は、まず`exploration.py`の`answer_from_full_text`（全関連セクションを`LLM_BATCH_CHARS`バッチへ分割し、並列で1回ずつ問い合わせて統合・pruneする「全文一括回答」）を試みます。これが回答を得られなかった場合のみ、`search`/`outline`/`read`/`finish`アクションを1手ずつ選ばせるステップ制限探索ループにフォールバックします（`LLM_MAX_STEPS`既定8・上限20。到達すると`exploration_limit_reached`を返し、未完了の回答は保存しません）。全文一括回答は2026-09-24に、旧来のステップ制限ループだけの構成（12,000字の読み取りでステップ予算を使い切っていた）を置き換える形で追加されました。

引用検証の規則: 各事実には短い原文引用・PDFの物理ページ番号・セクションを付けます。引用の存在、ページ、指定セクションはコードで検証します（`pdf.py`の`validate_evidence`、`EVIDENCE_INVALID`/`EVIDENCE_WRONG_SECTION`）。研究方法・データソース欄の引用が背景・参考文献・ENCePPチェックリスト・管理章にしか存在しない場合は`EVIDENCE_WRONG_SECTION`で保存を拒否します（補足事項・質問別回答はその章自体への質問もあるため許容）。データソース名は引用中に存在する名前に限定します。引用が存在することは、要約や使用区分の意味が正しいことの保証ではありません。

## CSVは直接取得せず、PDFは保存できる理由

対象と取得方法が異なります。

| 項目 | 公式CSV | プロトコルPDF |
|---|---|---|
| 発見元 | `/search/`の検索結果とExport操作 | 既知のStudy IDのStudy documents |
| robotsの扱い | `/search/`配下が明示的にDisallow | Studyページ、Study documents、`/system/files/`の対象PDFは同じ禁止対象ではない |
| 取得量 | 検索母集団全体のmetadata export | 一次判定上限（既定5件）以内の研究の最新版だけ |
| MCPの取得方法 | 直接HTTP取得を行わず、ユーザー起点の表示ブラウザ操作 | 識別可能なUser-Agent、間隔制御、robots確認付きでオンデマンド取得 |
| 保存目的 | ローカル検索用snapshot。原本とchecksumを保持 | 引用再確認・追加探索・版差替え検出。Study ID＋SHA256の不変IDで保持 |

PDF保存は「サイト全体のPDFを収集する」処理ではありません。ローカル候補が一次判定上限以内になってから、その研究のStudy documentsに掲載された最新版だけを取得します。取得時にも各URLのrobots判定、EMA HTTPSホスト制限、サイズ・ページ数制限、最低2秒間隔を適用します。CSV Exportは入口が禁止対象の`/search/`配下なので、同じHTTPクライアントから自動取得しない設計です。

## 最新版の選択・キャッシュ・取得制限

- Study IDとDrupalのnode IDは別物です。Studyページの実リンクから各タブへ移動します。
- 版の優先順位は、Study documentsの「Updated protocol」欄（クラス名に`-upd`を含む欄）の文書を、それ以外のプロトコル欄の文書より上にする（`ema.py`の`KIND_RANK`）。同じ分類内は、全候補に版があれば数値版番号、なければ全候補の文書日付、最後に利用可能な公開日で選択します。タイトル中の版・日付を用い、ファイルのアップロード日時だけでは比較しません。不十分な場合は`selection_reason`に不確実性を示します。PDF本文の全版を読み比べた意味的な最新版判定は行いません。`get_protocol`は候補一覧も返します。
- HTTPのHTML/PDFレスポンスキャッシュ（`EMA_CACHE_TTL_SECONDS`）とCSV snapshotの鮮度判定（`EMA_CATALOGUE_TTL_SECONDS`）は、どちらも既定30日間（2,592,000秒）です。EMAには固定の一括更新日がなく研究所有者が随時更新し、実CSVでも直近30日間に111/3,312件が更新されていました。最新性が重要な調査ではCSV再取得と`refresh=true`（`get_study`・`get_protocol`）を使います。起動時・期限切れ再取得時・`cleanup-cache`で期限切れを削除し、停止中に時刻通り削除する常駐ジョブは作りません。取得したPDFは`EMA_PROTOCOL_DIR`にID付きで保持し、全文テキストは恒久索引化しません。
- 構造化結果は永続保存します。URL、版、PDFのSHA256、schema、parser、prompt、モデル・provider設定からfingerprintを生成し、解析時はDocuments/PDFをTTLに従い再検証して同一URLの差し替えも検出します。通常検索はネット通信しないので、最新版とは`protocol_source`に記録した確認時点のものです。
- 1プロセス内のHTTP同時数1、最小2秒間隔、タイムアウト30秒、最大3回リトライです。Retry-Afterを優先し、60秒を超える待機指定は再試行可能時刻の目安付きエラーとして返します。robots.txtを確認し、取得先／リダイレクト先はEMA CatalogueのHTTPSに限定します。
- PDFは50 MiB／1,500ページ／抽出500万文字まで。OCR・暗号化PDFは対象外です。

```powershell
.venv/Scripts/ema-rwe analyze 1000000479 --refresh
.venv/Scripts/ema-rwe cleanup-cache
```

## 内部LiteLLMと追加APIキーなしの違い

目指す情報と保存形式は共通ですが、動作・探索範囲・回答品質まで同じではありません。

```text
追加APIキーなし:
質問 → Codex／Claude Codeが検索計画 → MCPが検索・PDF本文を返す
     → 呼出元LLM（またはSonnetサブエージェント）が読む → cache_*で引用検証・DB保存 → 比較表

内部LiteLLM:
質問 → 呼出元がMCPを操作 → MCPが検索・PDF取得
     → 設定先LLM APIで共通抽出／全文一括回答 → MCPが引用検証・DB保存
     → 呼出元が全件を集約して比較表を提示
```

| 観点 | 追加APIキーなし（呼出元LLM） | 内部LiteLLM |
|---|---|---|
| 強み | 会話の背景や追加条件を使い、検索式や探索方針を柔軟に変更できる。追加認証不要 | 呼出元と別のモデル・プロバイダを選べる。抽出手順をMCP内の共通プロンプトで管理し、処理を委譲できる |
| 費用・制限 | MCP用の追加API課金なし。ただし呼出元側の契約・利用量を消費し、本文が会話コンテキストを使う（実測 q1: 約$22） | 設定先のAPI利用料・利用制限が追加される（実測 q1: 呼出元$4.00 + プロバイダ数十セント） |
| 運用上の弱み | 呼出元が複数ツール・ページ送り・保存を最後まで実行する必要がある | 認証・モデル・リージョン等の設定が必要。API障害、レート制限への対応が必要 |
| 追加探索 | 呼出元が利用可能なツールと会話の制限の範囲で探索 | 指定PDFの関連セクションを一括で読んで回答。回答が得られない場合のみ`search`/`outline`/`read`のステップ制限ループ（既定8、最大20） |
| 情報の送信先 | 返されたPDF本文・質問は呼出元LLMの提供元へ送られる | PDF抜粋・質問が設定先プロバイダへ送られる。回答は呼出元にも返る |
| 品質・再利用 | モデル・読み方に依存。引用検証、PDF保存、JSON Schema、DB再利用は共通 | 同左。盲検採点では両モードの抽出品質は同程度〜僅差で、優劣は一定していない（各質問1回、[docs/validation.md](docs/validation.md)の2026-09-25） |

内部方式に渡るのはツールに指定した質問とPDF情報であり、呼出元との会話履歴全体は自動では渡りません。既知の条件は質問に含めてください。APIキー単独では有効になりません（`LLM_MODEL`と`litellm`または`LLM_BASE_URL`が必要）。BedrockのIAM認証など、APIキー文字列を使わない接続もあります。

## 環境変数の全一覧

`config.py`の`Settings`が正本です（既定値付き）。ただし辞書のパス（`EMA_DRUG_DICTIONARY_PATH`は`drugs.py`、`EMA_TERMINOLOGY_PATH`は`terminology.py`）は`Settings`の外で読みます。

| 環境変数 | 既定値 | 内容 |
|---|---:|---|
| `EMA_DB_PATH` | チェックアウト内`data/ema.sqlite3`、それ以外はユーザーデータディレクトリ | SQLite DBのパス |
| `EMA_CACHE_DIR` | ユーザーキャッシュディレクトリの`http` | HTTPレスポンスキャッシュ |
| `EMA_REQUEST_INTERVAL_SECONDS` | `2`（最小2.0に丸め） | HTTP取得の最小間隔 |
| `EMA_HTTP_TIMEOUT_SECONDS` | `30` | HTTPタイムアウト |
| `EMA_CACHE_TTL_SECONDS` | `2592000`（30日） | HTTPレスポンス（HTML・PDF）キャッシュの期限と、`get_study`が詳細ページを再確認する間隔 |
| `EMA_CATALOGUE_TTL_SECONDS` | `2592000`（30日） | ブラウザ再取得を推奨するまでの期限 |
| `EMA_MAX_SCREENING_STUDIES` | `5`（1〜1000） | 一次判定でPDF取得・全件解析へ進める最大研究数 |
| `EMA_MAX_COMPARISON_STUDIES` | `5`（1〜1000） | 比較表へ掲載する最大研究数 |
| `EMA_MAX_LISTED_CANDIDATES` | `50`（1〜1000） | `needs_narrowing`時に`candidates`一覧を返す最大件数 |
| `EMA_USER_AGENT` | `ema-rwe-mcp/0.5.3` | EMAへのHTTPリクエストのUser-Agent |
| `EMA_RESEARCH_BUDGET_CHARS` | `40000` | 呼出元向けの追加探索応答の文字数予算 |
| `EMA_SEARCH_BUDGET_CHARS` | `20000` | 呼出元向けのPDF全文検索応答の文字数予算 |
| `EMA_PROTOCOL_DIR` | DBと同じ親フォルダ内の`protocols` | 保持するPDF/JSONと見出しの英訳（`.headings`）の保存先 |
| `EMA_IMPORT_DIR` | `EMA_DB_PATH`と同じ親フォルダ内の`imports` | CSV取込のルート（`studies/`・`source_type/`） |
| `EMA_TERMINOLOGY_PATH` | 未設定時はデータフォルダ内`dictionaries/*.json`（既定では存在しない） | 利用者の概念辞書。ファイルまたは`*.json`を含むフォルダ |
| `EMA_UNMATCHED_LOG_PATH` | `EMA_DB_PATH`と同じ親フォルダ内`terminology_unmatched.json` | 辞書に一致しなかった質問・検索語のログ。辞書を設定しているときだけ記録（Git管理外） |
| `EMA_DRUG_DICTIONARY_PATH` | `EMA_DB_PATH`と同じ親フォルダ内`ema-medicines.json` | 公式EMA医薬品辞書 |
| `LLM_BACKEND` | `compatible` | `compatible`（Chat Completions互換）または`litellm` |
| `LLM_BASE_URL` | (空) | `compatible`時の接続先ベースURL |
| `LLM_API_KEY` | (空) | 接続先APIキー |
| `LLM_MODEL` | (空) | モデルID。LiteLLMは`<provider>/<model-id>`形式 |
| `LLM_API_VERSION` | (空) | Azure OpenAI従来APIのAPIバージョン |
| `LLM_MAX_STEPS` | `8`（上限20） | `research_protocol`のフォールバック探索ループの最大ステップ数 |
| `LLM_REASONING_EFFORT` | (空) | LiteLLMの`reasoning_effort`（例: `high`） |
| `LLM_MAX_TOKENS` | `0`（LiteLLMのモデル表を参照） | `max_completion_tokens`の上限 |
| `LLM_BATCH_CHARS` | `300000` | サーバー側抽出・全文一括回答の1バッチあたり文字数 |
| `LLM_CONCURRENCY` | `4` | サーバー側抽出と、内部探索の全文一括回答の並列数 |
| `LLM_WAIT_SECONDS` | `120` | `analyze_protocol`が`status=extracting`を返すまでの待機秒数 |

## 内部LiteLLM経路の設定例

```powershell
uv sync --extra dev --extra llm
```

| 接続先 | `LLM_MODEL` | 認証・追加設定 |
|---|---|---|
| Gemini | `gemini/<model-id>` | `LLM_API_KEY`または`GEMINI_API_KEY` |
| Anthropic | `anthropic/<model-id>` | `LLM_API_KEY`または`ANTHROPIC_API_KEY` |
| Azure OpenAI（v1 API、`.../openai/v1`） | `openai/<deployment-name>` | `LLM_API_KEY`、`LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1`。`LLM_API_VERSION`は空。reasoningモデルは`LLM_REASONING_EFFORT`、上限は`LLM_MAX_TOKENS` |
| Amazon Bedrock | `bedrock/<model-or-inference-profile-id>` | AWS認証チェーン（プロファイル、ロール、アクセスキー等）と`AWS_REGION_NAME`。IAM認証では`LLM_API_KEY`を設定しない |

Azure OpenAIの従来deployments APIやOpenAI直結の設定例、LiteLLMの接続仕様、内部探索のステップ上限・検証範囲は[docs/exploration.md](docs/exploration.md#litellm経由の内部探索)を参照してください。APIキーは共有する設定ファイルに直書きせず、起動元の環境変数で渡します。

## MCPツール一覧

stdioで18個のToolを公開します（`src/ema_rwe/mcp/server.py`）。

| Tool | 主な引数・動作 |
|---|---|
| `search_studies` | `query`, `limit=5`（1〜20。返す件数は`EMA_MAX_COMPARISON_STUDIES`でも切られ、`results_are_preview`が付く）, `darwin_only=false`, `status`, `analyzed_only=false`, `synonyms`, `codes`, `filters`, `role=any`, `detail=compact`, `match_scope=concept`, `analogous_terms`。通信なし。各行に`match_basis`・`matched_terms`・`matched_term_sources`。0件なら`analogous_fallback`（類縁概念・relation・件数、絞り込みで消えただけなら`concept_filtered_out`）。`match_scope=analogous`は類縁概念だけで検索し、依頼概念に一致する研究を除く。複数語は同一列内での近傍一致（NEAR距離はmax(3, 語数−1)。検索語そのものは元の文字列の機能語も数えて距離を決める）。5語以上の自由文は単語へ分解する（`compare_protocols`の検索語は分解しない）。`role`（outcome／condition／exposure）で役割の列に限定する（題名は常に含む。outcomeは目的とPDF由来の定義、exposureはPDF由来のデータソースも含む）。`detail=full`で説明文・由来・完全な展開を返す。`filters.data_source_types`はclaims／ehr／registry／othersの種別タグで絞る |
| `get_study` | `study_id`, `refresh=false`。研究種別とData source typeを各タブで確認 |
| `get_protocol` | `study_id`, `version="latest"`, `download=true`, `refresh=false`。取得したPDFのテキスト層（`text_layer`: full／partial／none）を判定して研究に記録する |
| `analyze_protocol` | `study_id`, `force_refresh=false`, `offset=0`, `max_chars=30000`, `detail=summary`。英語以外のプロトコルで見出しの英訳が無く内部LLMも無いときは、抽出の前に`status=needs_heading_translation`と`headings`を返す |
| `cache_heading_translations` | `protocol_id`, `translations`（`{見出し: 英訳}`、翻訳できなければ`{}`）。英語以外のプロトコルの見出しの英訳を保存する。一覧に無い見出し・改行を含む訳・300字超の訳は捨てて`ignored_entries`で数を返す。英語のプロトコルは`INVALID_INPUT` |
| `cache_protocol_analysis` | `study_id`, `fingerprint`, `analysis`, `coverage_complete=false`, `batch_offset`。バッチごとに`batch_offset=<offset>`で途中保存し、最後のバッチで`coverage_complete=true`を渡すと全バッチを統合して保存する |
| `compare_protocols` | `question`, `queries`, `filters`, `source_preference`, `darwin_only=false`, `synonyms`, `codes`, `role`, `study_ids`, `match_scope=concept`, `analogous_terms`, `category_terms`, `blocks`, `check_protocols=0`（0〜20。`needs_narrowing`で候補一覧が返る状態で、`study_ids`なし・`match_scope=concept`のときだけ確認する）。概念ブロック（ブロック内OR・ブロック間AND）を全列で検索し、役割は順位付けに使う。候補は切り詰めずに、公開プロトコルなし・テキスト層なし（`protocol_text_layer=none`）を最後に、固有語一致→役割の列→CSVのプロトコル所在（`protocol_listed`）→研究タイプ→統合順位で並べ、`rank_features`を返す。各検索語は1つの語句・医薬品名として語全体で照合する。医薬品の検索語は`medicines.py`で成分の上位クラス（カテゴリー語）やクラスの所属薬（検索語）へ展開し、加えた語を`medicine_expansion`（1問あたり100語まで、超過分は`omitted`）で返す。`check_protocols=N`は上位N件のStudy documentsを確認してプロトコルのない研究を最後に回す（PDFは取得しない）。0件時の`analogous_fallback`と類縁スコープは`search_studies`と同じ（複数ブロックでは`not_available_for_blocks`）。単一ブロックで0件のとき、呼出元が渡した第5レベルATCコードがあれば、同じ第4レベルのクラス（broader）とカタログ上の所属薬（sibling）を類縁概念に加える。比較表の先頭行は「一致の根拠」。一次判定上限以内なら全PDFと下書きJSONを保存。上限超過時は`facets`（国・種別・デザイン・Medicinal condition）と、`EMA_MAX_LISTED_CANDIDATES`以内なら`candidates`一覧を返し、`next_action`で種別と実施国の質問を指示。ユーザーが一覧から選んだ`study_ids`を渡すと、その研究だけを一次判定に進める。`source_preference`はPDF判定後に優先／限定 |
| `get_protocol_comparison` | `comparison_id`, `selected_study_ids`（ユーザーが選択した場合）, `detail=compact`。全件の保存済み抽出・質問別回答を集め、JSONと比較表を更新 |
| `catalogue_status` | CSV snapshotの有無・最終取込時刻・期限・`studies`／`source_type`出力先・取込済み種別（`source_type_imports`）、読み込み中の利用者辞書（`dictionaries`。設定不備は`error`）を返す。通信なし |
| `import_catalogue_csv` | `filename`, `column_map`。`EMA_IMPORT_DIR/studies`または`source_type`直下の公式CSVを検証し、Non-interventional studyだけを登録。`source_type/`のファイルは名前の種別でタグ付け |
| `refresh_drug_dictionary` | `force=false`。検索応答の`query_expansion.drugs_need_refresh`（`detail=full`の展開では`needs_refresh`）が真のときに公式EMA医薬品辞書を再取得 |
| `plan_study_search` | `question`, `use_llm=false`。検索は実行しない。研究デザイン語・医薬品・利用者辞書の概念を展開し、辞書に一致しない日本語の質問には`status=needs_client_translation`と`client_expansion`（ICD-10を手がかりに英語名・言い換え・コード候補・類縁概念を生成させる指示）を返す。`use_llm=true`はサーバー側LLMが同じ指針で生成する |
| `list_local_protocols` | `study_id`。保存済みPDFの各版と不変`protocol_id`一覧。通信なし |
| `get_protocol_outline` | `protocol_id`, `offset=0`, `limit=100`, `detail=compact`。全文の章一覧（section_id・ページ・章・role。親子・前後関係・構造警告は`detail=full`のとき） |
| `search_protocol_text` | `protocol_id`, `query`, `limit=10`, `synonyms`, `codes`, `max_chars`。初回除外した章も含むPDF全文検索 |
| `read_protocol_text` | `protocol_id`, `section_id`または`start_page`/`end_page`（1〜5ページ）, `offset=0`, `max_chars=12000`。`next_offset`で続きを読む |
| `research_protocol` | `protocol_id`, `question`, `force=false`。保存回答の再利用、全文一括回答、または呼出元駆動のステップ探索 |
| `cache_protocol_answer` | `protocol_id`, `question`, `answer`。質問別の出典付き回答を検証・保存 |

CLIのサブコマンドはMCPのツールと同じ引数と既定値を取ります（`uv run ema-rwe <サブコマンド> --help`。`search`も既定で全研究を対象にし、`--darwin-only`でDARWIN EUに限ります）。

MCPの`instructions`文字列は要点のみに短縮しており、完全な手順とフィールド意味論は[docs/mcp-workflow.md](docs/mcp-workflow.md)が正本です（MCPリソース`ema-rwe://docs/mcp-workflow`としても返します）。検索応答は既定でcompact（説明文・由来を省略）、`catalogue`は状態・取込済み種別・`browser_refresh_recommended`だけを返し、別に`catalogue_action`を返します。

## ドキュメント索引

| ファイル | 内容 |
|---|---|
| [docs/spec/v0.1.md](docs/spec/v0.1.md)〜[v1.6.md](docs/spec/v1.6.md) | 仕様の正本（段階的な追加要件。v0.5：一致語の出所と類縁概念フォールバック、v0.6：クライアント翻訳の既定化、v0.7：長い検索語の扱い、v0.8：全列・階層つきの一次検索と順位付け、v0.9：ATC上位クラスのカテゴリー語（v1.0で置換）、v1.0：カタログ由来の医薬品名展開と`medicine_expansion`、v1.1：プロトコル所在・テキスト層による順位付け、名前を主キーにした医薬品の照合、v1.2：取得で分かった事実の別表保存・医薬品欄の補完・類縁概念の順位付け、v1.3：PASS表のクラス名とATCコード・文章からの補完の誤り防止・抽出規則の版、v1.4：手順書のMCPリソース・ツールの入力範囲の明示、v1.5：構造推定の汎用化（役割の三つの判断、ローマ数字の節番号、目次とレイアウトによる構造の補完、チェックリストの範囲）、v1.6：英語以外のプロトコルの見出しの英訳） |
| [docs/mcp-workflow.md](docs/mcp-workflow.md) | 呼出元エージェント向けの完全な手順（英語） |
| [docs/clinical-search.md](docs/clinical-search.md) | 日本語疾患名・薬剤名→英語・医療コードの展開、辞書の網羅性の限界 |
| [docs/comparisons.md](docs/comparisons.md) | 複数プロトコルの比較・絞込ワークフロー |
| [docs/source-types.md](docs/source-types.md) | PDF由来データタイプの判定・表示順序・公式分類との差異 |
| [docs/exploration.md](docs/exploration.md) | 同義語・章構造・追加探索ツール・LiteLLM接続例 |
| [docs/codex-setup.md](docs/codex-setup.md) | このチェックアウトのCodex MCP設定の解説 |
| [docs/csv-profile-20260912.md](docs/csv-profile-20260912.md) | 実CSVの構造・欠損率調査 |
| [docs/data-source-linkage-20260912.md](docs/data-source-linkage-20260912.md) | Data Sources CSV結合検証（調査履歴） |
| [docs/validation.md](docs/validation.md) | 実サイト検証・自動検証・実測コスト・未検証事項 |
| [docs/research/](docs/research/) | 個別調査記録（用語マイニング、配布方式比較、PDF読解コスト、医療用語体系の比較等） |

実サイト検証と既知の制限は[docs/validation.md](docs/validation.md)を参照してください。仕様の10〜20プロトコルによる人手精度評価は未実施です。
