# EMA RWE MCP — 開発者向け

エンドユーザー（このMCPを使って研究を調べる人）向けの使い方は[README.md](README.md)を参照してください。本ファイルはこのMCPサーバー自体を開発・改修する人向けの内部仕様です。

公開版は0.6.0（内部履歴では0.1〜1.31の仕様段階を経ている）。`docs/spec/v0.1.md`〜`v1.31.md`が仕様の正本、[docs/mcp-workflow.md](docs/mcp-workflow.md)が呼出元向け手順の正本、開発ルールは[AGENTS.md](AGENTS.md)です。

## 開発用セットアップ（checkout）

Python 3.12以上とuvを使います。

```powershell
uv venv --python 3.12
uv sync
git config core.hooksPath .githooks
$env:EMA_DB_PATH = "<checkout>/data/ema.sqlite3"
$env:EMA_CACHE_DIR = "<checkout>/cache/http"
$env:EMA_IMPORT_DIR = "<checkout>/data/imports"
uv run ema-rwe --help
```

開発用のツール（pytest、ruff）は`dev`の依存グループにあり、`uv sync`と`uv run`が既定で入れます。`git config core.hooksPath .githooks`は、pushの前に下記のチェックを自動で走らせる設定です（cloneごとに1回）。内部LLM経路も試す場合は`uv sync --extra llm`とします。別の場所にcloneした場合はパスを置き換えてください。CLIは各OSで`uv run ema-rwe`から実行します。

`EMA_DB_PATH`未設定時は、`config.py`の`default_data_dir()`がチェックアウト内（`pyproject.toml`がある場所）かどうかを判定し、チェックアウト内なら`<repo>/data/ema.sqlite3`（Git管理のカタログDB）を、そうでなければOSのユーザーデータディレクトリ（初回のみ同梱データを複製）を使います。キャッシュは`EMA_CACHE_DIR`未設定時にOSのユーザーキャッシュディレクトリが既定です。`.env.example`は設定例のドキュメントであり、**このリポジトリは`.env`を自動読込しません**（`python-dotenv`等は使用していません）。環境変数はシェルまたはMCPクライアントの`env`で渡してください。

## レイアウト

`src/ema_rwe/`配下で、Core・CLI・MCPアダプタを分離しています。

| モジュール | 役割 |
|---|---|
| `service.py` / `cli.py` / `mcp/server.py` | 共通処理の入口（`Service`）、CLI、stdio MCPの18ツール |
| `catalogue.py` / `screening.py` / `protocols.py` / `extraction.py` | `Service`のメソッドの本体。順に、カタログの状態・取込・補完、検索・候補の集計・検索語の計画、研究ページとプロトコルの取得・PDFの読取、抽出・見出し英訳・質問別回答の保存 |
| `unmatched.py` | 未一致語の記録（利用者辞書があるときだけ書く） |
| `storage.py` / `archive.py` | SQLite永続化、CSV取込、解析・読み方の履歴、不変ID付きPDFの保持 |
| `pdf.py` / `domain.py` | PDF構造・節の役割・引用検証、抽出スキーマと例外型 |
| `llm.py` / `exploration.py` | サーバー側抽出、見出し英訳、質問別探索・回答保存 |
| `terminology.py` / `vocabulary.py` | 利用者辞書、コード表記、研究デザイン・手法の語彙 |
| `drugs.py` / `medicines.py` | EMA医薬品辞書、成分・製品・クラスの展開、医薬品欄の補完 |
| `selection.py` / `ranking.py` / `source_types.py` | 絞り込み、候補順位、PDF由来データタイプの評価 |
| `comparison.py` | 全screening研究のPDF/JSON/Markdown出力。失敗・未完了を保持 |
| `ema.py` / `http.py` | Study documentsの解析・最新版選択、robots・間隔・リトライ制御 |
| `config.py` / `snapshot.py` / `catalogue_update.py` | 環境設定・同梱データ更新、月次スナップショットの作成・検証、公開されたスナップショットの取り込み |

検索・医薬品照合の詳細は[展開と順位付け](docs/search-logic.md)、保存・抽出の変更時は関連仕様と以下の節を確認してください。

## データ処理の流れ

質問の展開から候補の絞り込み、PDF解析、比較出力までの全体像です。候補数は全検索式で見つかった研究の重複除外unionで数え、一次判定上限と比較表の上限を別々に扱います。PDF未掲載・取得失敗・未確認事項も出力に残します。

```mermaid
flowchart TD
    A[日本語で質問] --> B1[疾患名：呼出元がICD-10を手がかりに英語名・言い換え・類縁概念を生成]
    A --> B2[薬剤名：呼出元が英語名へ翻訳し、EMA医薬品辞書とカタログの記載で製品名・クラス・所属薬へ展開]
    B1 --> C[全検索式の候補研究を重複除去して候補数を集計]
    B2 --> C
    C --> D{候補数}
    D -->|0件| J[類縁概念と件数を提示]
    J -->|同意| K[類縁概念で検索]
    K --> D
    D -->|一次判定上限超過| E[データソース種別と実施国を質問]
    E --> C
    D -->|一次判定上限以内| F[全候補の最新PDFを取得・抽出]
    F --> G{比較表の上限}
    G -->|超過| H[比較に載せる研究の選択を質問]
    G -->|上限以内| I[比較表とJSONを提示]
    H --> I
```

## 内部の探索ロジック

検索語を作る呼出元と、語の展開・ローカル検索・順位付けを行うサーバーの役割を示します。検索対象はカタログのメタデータで、各研究のコードや定義は候補選択後にPDFの引用で確認します。

```mermaid
flowchart TD
    Q[質問] --> P[plan_study_search]
    P -->|デザイン・手法の語| V[内蔵の語彙表で英語へ]
    P -->|疾患名| D[呼出元がICD-10を手がかりに英語名・言い換え・カテゴリー語・類縁概念を生成]
    P -->|薬剤名| M1[呼出元が英語のINN・EU/米国の製品名へ翻訳]
    M1 --> M2[サーバーがカタログの「ATCコード 名称」とEMA医薬品辞書で展開]
    V --> S[compare_protocols：概念ごとのブロックで全列を検索]
    D --> S
    M2 --> S
    S --> R[順位付け：候補は削らない]
```

概念間はAND、同じ概念の検索variant間はORです。`compare_protocols`の`role`は役割に対応する列での一致を順位へ反映し、検索対象をその列だけに限定しません。疾患・薬剤ごとの展開規則と順位の詳細は[展開と順位付け](docs/search-logic.md)を参照してください。

## テスト・lint・ビルド

```bash
uv run scripts/check.py
```

このコマンドが、pushの前に通すべきチェックを順に実行します（ホスト型のCIは使っていません）。最初に失敗した段階で止まり、その名前を表示します。全体で20〜30秒です。

| 段階 | 内容 |
|---|---|
| `versions` | 版の表記が、`pyproject.toml`と同じ値で全箇所に書かれている（`uv.lock`、README類、`config.py`、`.env.example`、サンプル設定、CHANGELOGの最新の版の見出し） |
| `lock` | `uv.lock`が`pyproject.toml`と合っている |
| `lint` / `format` | `ruff check`と`ruff format --check`（`src`、`tests`、`scripts`） |
| `tests` | `pytest`（実stdioのMCPテストを含む） |
| `gold` | 抽出の正解データ（4つのプロトコルの組入れ・除外基準60項目とアウトカム定義40項目）が、すべて見つかり、読み手に渡り、根拠として受理される（spec v1.20、v1.21、v1.24）。プロトコルはcloneごとに1回`uv run scripts/extraction_eval.py fetch`で取得する。未取得のときは`--allow-missing-gold`。詳細は[tests/fixtures/extraction_gold/README.md](tests/fixtures/extraction_gold/README.md) |
| `build` / `wheel` | `uv build`と、wheelに全モジュールと同梱データ（カタログDB、医薬品辞書、手順書）が入っていること |

編集中は`uv run scripts/check.py --no-build`でビルドを省けます。個別に実行する場合は`uv run pytest -q`、`uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`、`uv build`です。

`.githooks/pre-push`は、pushのたびにこのチェックを実行し、失敗したらpushを止めます。有効にするには、cloneごとに1回`git config core.hooksPath .githooks`を実行します（有効にしていないcloneからのpushは確認されません）。1回だけ省くときは`git push --no-verify`です。Windowsでは、Git for Windowsに付属のshがhookを実行します。

通常テストはネット通信せず、固定HTML断片・合成PDF・`MockTransport`を使用します。CSV取込・検索・対象外排除・新版選択・PDF抽出・引用検証・解析保存・再検索・差し替え検出・HTTPリトライ・期限切れ削除に加え、**MCP stdio初期化／discovery／実行／入力エラーの実stdio MCPテスト**を含みます。ツールのスキーマを変更した場合は必ずこれらを実行してください。依存関係は`uv.lock`に固定しています。検証結果と未実施の確認は[docs/validation.md](docs/validation.md)に記録してください。

## 配布

wheelにはカタログDB、EMA医薬品辞書、呼出元手順書を同梱します。チェックアウト外では初回起動時にOSのユーザーデータディレクトリへ複製します。手順書はMCPリソース`ema-rwe://docs/mcp-workflow`からも取得できます。

既定DBのカタログは2つの経路で更新します。どちらもカタログの表の置き換えは`storage.refresh_from_bundle`が行い、失敗したときは理由をstderrにwarningとして記録して元のカタログのまま動きます。

1. 新しい版のパッケージが同梱するDB。1プロセスにつき1回確認します。
2. GitHub Releasesに公開した月ごとのスナップショット（`catalogue_update.py`、spec v1.18、v1.26）。サーバーは起動時にデーモンスレッドで確かめ（1日1回まで）、検証してから取り込みます。終了はこのスレッドを待ちません。作業フォルダはDBと同じフォルダの`ema-rwe-update-*`で、止まった更新が残したもの（6時間より古いもの）は次の更新の開始時に消えます。

- 同梱DBの取込日時が新しければカタログの表だけをトランザクションで更新します。同じスキーマ版でなければ更新せず、利用者のCSVが新しい場合も保持します。
- PDF由来の事実は`protocol_observations`に分離して保持します。手元にない項目を補い、医薬品の補完値は出所ごとに`EXPOSURE_RULES`の版が新しい場合だけ置き換えます。
- 解析・回答・見出しの読み方は保持します。`get_study`で更新したカタログ行は同梱値へ戻り得ますが、PDF観測は保持します。
- `EMA_DB_PATH`で指定したDBとcheckoutのDBは、どちらの経路でも自動更新・統合の対象外です（`ema-rwe update-catalogue`で更新します。MCPクライアントのenvと同じ`EMA_DB_PATH`を設定したシェルで実行します。CLIは自分の環境のDBを更新するためです）。医薬品辞書は`refresh_drug_dictionary`で更新します。更新失敗時は元のカタログで起動します。

`.mcp.json`（このリポジトリ直下、Git管理外）は各自の登録で、開発中は`uv run --directory /path/to/repo ema-rwe-mcp`によりチェックアウトを直接起動できます（作業ツリーの未コミット変更もそのまま反映されます）。公開版を確かめるときは`.mcp.json.sample`と同じ`uvx`の形にします。`.mcp.json.sample`（Git管理対象）はエンドユーザー向けで、`uvx --from git+https://...`によりcloneなしでリモートのコードを取得・起動します。両者は起動対象（ローカル作業ツリー vs. リモートのgit ref）が異なる点に注意してください。

リポジトリを公開する場合は次を行います。

1. `.gitignore`を再確認する（現状は`data/`配下のうちカタログDB・医薬品辞書・辞書の記入例・関連語の判定記録の4ファイルと`imports/`の空フォルダだけを追跡し、他はすべて除外。PDF・DB・キャッシュ・`.env`・`docs/agent_brief/`・`docs/agent_report/`も除外済み）。
2. リリースタグ（`v<pyproject.tomlの版>`）を打つ。手順は[docs/release.md](docs/release.md)。
3. 月ごとのカタログは、アプリとは別に`data-<YYYYMMDD>`のリリースとして公開する（`scripts/build_catalogue_snapshot.py`、手順は同じ文書）。利用者のデータフォルダのDBは、サーバーが起動時に公開スナップショットを取り込む（spec v1.18）。同梱DBは、アプリのリリースのときに最新のスナップショットで置き換える。同梱DBが最新のスナップショットと同じ日付のエクスポートから作ったものなら、置き換えずにそのまま使う（spec v1.19、docs/release.md）。

PyPI公開やGitHub Releaseへのwheel添付など他の配布経路の比較検討は[docs/research/202609160750_mcp_distribution.md](docs/research/202609160750_mcp_distribution.md)を参照してください。

## カタログ取込の内部仕様

`import_catalogue_csv(filename)`は`EMA_IMPORT_DIR`（既定は`EMA_DB_PATH`と同じ`data/`配下の`imports/`）の`studies/`または`source_type/`直下のファイル名のみを受け付けます（移行用にルート直下も読みます）。任意パスは受け付けません。

- **必須列**: `Study ID`、`Official title and acronym`（公式CSVでは`Title`）、`Study type`。別名の対応は`storage.py`の`ALIASES`を参照してください。不一致はエラーになり、黙って0件登録しません。列名が異なる場合はCLIでは`--column-map ./columns.json`、MCPの`import_catalogue_csv`では`column_map`（辞書）を使います（例: `{"study_id":"Study identifier","title":"Title","study_type":"Type"}`）。
- **Non-interventional判定**: `Non-interventional study` / `Non-interventional`と明示された研究だけ取り込みます。種別不明・介入研究は除外します。URL中の`content_type:darwin_study`は研究レコード種別で、DARWIN EU研究限定とは別です（`darwin_only`は独立したフラグで判定）。
- **種別タグ（`data_source_types`）**: `Data sources (types)` / `Data source type`列があれば直接取り込みます。ただし2026-09-12/13時点のStudies exportにはこの列がないため、EMA検索画面でData source typeをclaims/EHR/registryに限定してexportしたCSVを`source_type/`へ置き、**ファイル名に埋め込んだ種別だけをタグの根拠**とします。ファイル名は`<日付>_<claims|ehr|registry>_export-data.csv`とし、種別トークンが1つだけ含まれる必要があります。日付は`YYYYMMDD_`の接頭辞で書きます（`catalogue_status`の`snapshot_aligned`と、スナップショットの作成がこの日付を読みます）。全研究のエクスポートも`<YYYYMMDD>_all_export-data.csv`のように同じ日付で始めます。種別exportを取り込む前の研究はすべて`others`として扱われます。`source_type/`のファイルは各研究の`data_source_types`に種別を追加し（複数種別は累積。名前に種別を含むファイルは他のフォルダに置いても、名前の種別で付く）、その後に全件exportを再取込してもタグは保持されます。
- **区切り**: 複数値の区切りは`|`・`;`・改行です。値内部のカンマは分割しません。
- **プロトコル所在（`protocol_listed`）**: `Protocol file(s)`・`Protocol file(s) - URI`・`Protocol URL`（2026年10月までのexport）、`Initial protocol file - URI`・`Updated protocol file(s) - URI`（2026年11月以降）のいずれかに値があれば`true`、すべて空なら`false`、列が無ければ未設定です。順位付けにだけ使い、最新版の選択には使いません（最新版はStudy documentsで選びます）。
- **再構築**: `data/imports/{studies,source_type}/`にexportを置いて`uv run ema-rwe import-all`を実行すると、`studies/`、`source_type/`の順にすべて取り込み、最後に`VACUUM`でDBファイルの空き領域を詰め直します（`merge-observations`も同じ。同梱DBは、この2つのコマンドで作ります）。出力の`vacuum`に前後のファイルサイズ（`bytes_before`、`bytes_after`）が入ります。
- **医薬品欄の補完**: `uv run ema-rwe backfill-protocols [--interval 60] [--limit N] [--no-download] [--reextract]`。医薬品欄が空の研究に、題名・説明・目的の既知の医薬品名（通信なし）と、CSVにプロトコルの所在がある研究のプロトコルのPASS情報表（Active substance・Medicinal product）の既知の医薬品名・カタログのクラス名・欄に書かれたATCコード・ページを補います（spec v1.2、v1.3）。研究の略称と同じ名前、検査値として書かれた物質名、直後にreceptor・inhibitorなどが続く名前は補いません。1件ずつ、研究の間を`--interval`秒空け、429や通信障害で止まり（HTTP層の再試行の後）、読み終えた研究（`backfill_done`）は飛ばして再開します。同梱DBを作るときは、作業用のDB（`EMA_DB_PATH`）で実行してから（医薬品辞書は`EMA_DB_PATH`の隣から読むので、`EMA_DRUG_DICTIONARY_PATH`にチェックアウトの`data/ema-medicines.json`を指定する。辞書が無いと題名の医薬品名の多くを見落とします）、`EMA_DB_PATH`を外して（チェックアウトの`data/ema.sqlite3`を対象にして）`uv run ema-rwe merge-observations <作業用DB>`で同梱DBに観測を加えます（配る項目だけを移し、索引も作り直します。失敗はエラーとして返します）。抽出の規則を直したときは、作業用DBで`--reextract --no-download`を実行して保存済みのPDFから読み直し、`medicines.EXPOSURE_RULES`の該当する出所の版を上げます（同梱DBの新しい版の結果が、利用者のDBの古い結果を置き換えます）。
- **upsert**: 研究ID単位のupsertです。今回のCSVにない既存研究は削除しません。元CSVのバイト列、SHA256、ファイル名、取込時刻を保存します。
- **連絡先の非索引化**: 原本CSVに連絡先が含まれる場合があります。原本は検索対象から分離され、連絡先専用列はDB／FTS／検索結果には入れません。
- **Data Sources CSVは不要**: カタログの種別タグはローカル候補の絞り込みだけに使い、定義ごとのデータタイプは候補PDFの該当用途からLLMで判定して公式のStudy分類と分けて保存します（[docs/source-types.md](docs/source-types.md)）。
- **Playwright MCP経路の理由**: `/search/`配下はrobots.txtで明示的にDisallowされているため、このMCP自身は検索結果ページやExport URLをHTTPでクロールしません。代わりに、Microsoft公式[Playwright MCP](https://github.com/microsoft/playwright-mcp)を呼出元用の別MCPとして併用し、ユーザーが開始した調査で公式の画面手順（`Export Results`）を1回だけ実行します。ブラウザをPythonサーバーへ埋め込まず画面操作を呼出元へ分離することで、DOM変更時にLLMが要素を再探索でき、検索・検証・保存処理も単独で利用できる設計です。`EMA_CATALOGUE_TTL_SECONDS`（既定30日）以内で候補が0件の場合は、先に英訳・同義語・コード・絞込条件を見直し、同じsnapshotの再取得を避けます。

## 抽出と検証の仕組み

**読む範囲の決め方（`pdf.py`）**: PDFにしおり（ブックマーク）があれば、それを一次の目次として使います。各ページに「そのページを含む最も深いしおり」と「その最上位の章」を付け、本文の見出し検出で役割が決まらない章にはしおりの題名から役割を与えます（`apply_bookmarks`）。抽出で読む節は`reading_order`が決め、しおりがある場合はページ順に、本文の章と、付録のうち本文が参照しているもの（「Annex 3」「Appendix V」）とコードリスト・変数定義の題名を持つものだけを読みます（履歴書・ENCePPチェックリスト等は読みません）。しおりのないPDFでは、印字ページのずれを確かめた目次で節の始まりを決め、目次も無ければ本文より大きいか太字で章番号が続く行を章見出しとし、関連セクションをすべて読みます（spec v1.5）。節の役割は、読むか、引用を受理するか、抽出の指示の三つに別々に使います。質問別探索（`search_sections`）はしおりの章名に一致した語を最優先で採点します。実測では242頁のプロトコル（19786）の読取量が、しおりを使わない場合の44.8万字に対して11.1万字です（parser `structural-v24`、2026-10-06）。

**英語以外のプロトコル（spec v1.6）**: 役割の語は英語なので、本文が英語でない文書（英語の機能語が全語の8%未満）は、見出しとしおりの題名（`pdf.heading_texts`）の英訳から役割を判定します。`analyze_protocol`はPDFごとに一度だけ（parserの版が変わったら再び）言語を判定し（`extraction.py`の`_check_heading_language`）、内部LLMがあれば`llm.translate_headings`で英訳し、無ければ`status=needs_heading_translation`と見出しの一覧を返します。呼出元は`cache_heading_translations`で英訳を保存します（`{}`なら英訳なしで読む）。判定結果と英訳は利用者のDBの`protocol_readings`表にparserの版とともに置き（`Explorer.reading_record`。以前の版がPDFの隣に置いた`<protocol_id>.headings`は一度だけ取り込む）、`extract_pages(translations=...)`が各`Page.heading_translations`に渡すので、`sections`を呼ぶ全経路（抽出・引用検証・探索ツール）が同じ役割を使います。英訳は節の役割の判定（読むか、引用を受理するか、抽出の指示）だけに使い、本文の読解には使いません。本文の手がかり語（英語）は英語でない本文を読めないので、英訳でも役割の分からない節は読みます。英訳はfingerprintに入れません（比較表が翻訳前にfingerprintを記録するため）。英訳を別の内容で保存し直すと、同じトランザクションでそのPDFの保存済み解析を失効させ、途中のバッチと実行中の抽出も取り消します。解析は抽出時の読み方（英訳のハッシュ、`source.reading`）を記録し、保存はDBの今の読み方と一致するときだけ行う（`Repository.save_analysis(reading=...)`、不一致は`READING_CONTEXT_CHANGED`）ので、DBを共有する別のサーバープロセスが英訳を変えても古い解析は保存されません（spec v1.8）。質問別回答のキャッシュの鍵には英訳を含めるので、英訳の前に保存した回答は英訳の後に再利用しません。質問別の探索は、全文の回答、最初の検索、内部LLMの`search`／`outline`／`read`、引用の検証、鍵のすべてを開始時の英訳で行います。探索ツールの応答の`source.reading`はその応答を作った読み方で、呼出元は`research_protocol`の`reading`と違えば探索をやり直します（各ツールに読み方を渡して拒否する仕組みは無いので、呼出元が見落とした探索中のA→B→Aは保存時の照合をすり抜けます。spec v1.10）。

**抽出スキーマ v0.3（`domain.py`）**: `cohort`ブロックを追加しました。`inclusion_criteria[]`・`exclusion_criteria[]`（各基準を1事実として根拠付き）、`index_date`、`baseline_period`（連続加入・ルックバック）、`follow_up`（開始・終了・打ち切り）、`design_schema`（設計図の物理ページ`figure_pages`と、本文にある対応する時間窓の記述`time_windows[]`）です。図そのものは画像のため読みません。fingerprintに`schema: 0.3`が入るため、既存の保存済み抽出は次回`analyze_protocol`で再抽出されます。比較表には「コホート定義」「設計図（ページ・時間窓）」の行が加わります。

`analyze_protocol`は2つの経路を持ちます。どちらの経路でも、同じPDFは1プロセスで1回だけ解析し（`pdf.extract_pages`が新しい順に8件をメモリに保ち、同時の要求は1回にまとめます）、読み手に渡す節の本文は3つ以上続く空白を2つに詰めます（spec v1.12。節の分け方と引用の照合は変わりません）。MCPサーバーはツール呼び出しごとに所要時間・応答の文字数・PDF解析の有無をstderrに記録します（`tool=... ms=... chars=... pdf_parsed=...`。例外で終わった呼び出しも、`status`に例外の型名、`chars=0`で記録します。spec v1.26）。`uv run scripts/time_tools.py <研究ID>...`で、LLMなしのツール呼び出しの所要時間を測れます。

**呼出元抽出**（`LLM_MODEL`が未設定、または`LLM_BACKEND=compatible`で`LLM_BASE_URL`が未設定のとき）: 英語以外のプロトコルで英訳が無ければ、先に`needs_heading_translation`を返します。それ以外は`needs_client_extraction`と関連セクション・物理PDFページ・抽出JSON Schema・fingerprintを返します。呼出元は`offset`を進めながら`analyze_protocol(offset=next_offset)`で全バッチを読み、バッチごとに`cache_protocol_analysis(batch_offset=offset)`で途中保存します（サーバーは保存済みoffsetを`cached_batch_offsets`で返すので、コンテキスト圧縮後も既読分を再読しません。途中保存は利用者側の表`extraction_batches`に入るので、サーバーの再起動や別プロセス（CLIは呼び出しごとに別プロセス）でも失われません。サーバー側抽出も、終わったバッチを同じ表に保存します。進行中のタスクはメモリにあるだけですが、1バッチの失敗や再起動の後の`analyze_protocol`は、足りないバッチだけをプロバイダへ送ります（spec v1.14））。最後のバッチも`batch_offset`付きで保存してから`coverage_complete=true`を呼ぶと、サーバーが全バッチを統合して保存します。読んでいないバッチがあれば`INCOMPLETE_EXTRACTION`で読むべきoffsetを返し、`analyze_protocol`が返していないoffsetのバッチは受け付けません（spec v1.11）。読み残しの判定は、各バッチを保存した時点でそのoffsetに配信した範囲（`extraction_batches`のkind `read`）をたどるので、同じoffsetを別の`max_chars`で取り直しても、バッチが実際に読んだ範囲で判定します（spec v1.26）。バッチを1つも保存していない空の最後の呼び出しも、配信の記録があれば同じ判定をします。中身のある解析の全体を1回で渡す呼び方は、バッチが無くても受け付けます（spec v1.29）。`analyze_protocol(question=...)`のときは、抽出の指示に質問と`answer_schema`が加わります。呼出元は各バッチの`cache_protocol_analysis`に`question`とそのバッチの`answer`を渡し、最後の呼び出しが回答を結合して`research_protocol`と同じ鍵で保存します（`answer_saved`。空なら保存せず、`research_protocol`で探索します）。バッチに回答があるのに最後の呼び出しが同じ`question`を渡さないときは、何も保存せず`INVALID_INPUT`で断ります。最後の呼び出しは解析を保存してから回答を保存します（spec v1.26）。質問を渡しても、抽出はスキーマの全項目を埋めます。`analyze_protocol(force_refresh=true, offset=0)`は、その研究の途中バッチ（呼出元とサーバー側抽出の両方）をすべて消します（spec v1.26）。

**サーバー側抽出**（`LLM_MODEL`があり、かつ`LLM_BACKEND=litellm`または`LLM_BASE_URL`があるとき）: 関連セクションを`LLM_BATCH_CHARS`（既定300,000文字）以下のバッチに分割し、`LLM_CONCURRENCY`（既定4）並列でプロバイダに投げて統合します（`llm.py`の`split_batches`/`merge_extractions`）。抽出が`LLM_WAIT_SECONDS`（既定120秒）を超えるときは`status=extracting`を返すので、呼出元は同じ`analyze_protocol`をポーリングします。CLIの`analyze`は、プロセスの終わりで抽出が止まるので、終わるまで待ちます（spec v1.26）。保存前に`extraction.py`が`pdf.finalize_extraction`を呼び、その中の`prune_unverifiable`が証拠を検証します。隣のページに引用があればページを直し、データソース名が引用に無ければ名前を含む引用を加え、それでも原文と一致しない証拠だけを落として、件数と監査メモを`missing_information`に記録します。プロンプトはLLMに`evidence.section`を`null`のまま返すよう指示し（`llm.py`）、サーバーが引用の位置から節ラベルを導出します（`pdf.py`の`_derived_section`。呼出元が保存した引用も、nullのラベルだけを`with_derived_sections`で埋めます。spec v1.27）。抽出プロンプトは節ラベル省略・電報体の`value`/`definition`・JSON最小化を指示しつつ「事実・コード・条件の省略禁止」も明示しており、出力トークンを削減しながら証拠件数を落とさない設計です（実測は[docs/validation.md](docs/validation.md)の2026-09-24節を参照）。

**バッチの統合**（`llm.py`の`merge_into`、`spread`、`cap_lists`、`merge_answers`。呼出元抽出の`coverage_complete`とサーバー側抽出の両方）: 値が同じ項目（大文字と小文字、空白、記号の違いを除く）は、根拠を合わせて1つにします（8件まで。同じデータソースが「計画」と「使用」の両方で書かれていれば「計画」を残します）。まとめた後もスキーマの上限を超えるリストは、最初のバッチから詰めるのではなく、各バッチから順に1件ずつ採ります。どの深さのリストの項目にも、最初に現れたバッチを記録します（spec v1.23、v1.26）。質問別回答も同じ規則で、上限は60件です（spec v1.25）。上限で落とした件数と採り方は`missing_information`に書きます（例：`... only 20 are kept (schema limit), 6 omitted, taken in turn from the 5 batches.`）。

`research_protocol`（内部LLM設定時）は、まず`exploration.py`の`answer_from_full_text`（全関連セクションを`LLM_BATCH_CHARS`バッチへ分割し、並列で1回ずつ問い合わせて統合・pruneする「全文一括回答」）を試みます。これが回答を得られなかった場合のみ、`search`/`outline`/`read`/`finish`アクションを1手ずつ選ばせるステップ制限探索ループにフォールバックします（`LLM_MAX_STEPS`既定8・上限20。到達すると`exploration_limit_reached`を返し、未完了の回答は保存しません）。全文一括回答は2026-09-24に、旧来のステップ制限ループだけの構成（12,000字の読み取りでステップ予算を使い切っていた）を置き換える形で追加されました。探索はサーバーのタスクとして動き、`LLM_WAIT_SECONDS`を超えると`status=answering`を返します。同じ質問の呼び出しは動いている探索に合流し、探索が終わった後の最初の呼び出しが、`force`の有無に関係なくその結果を受け取ります。`force=true`が新しい探索を始めるのは、探索が動いておらず受け取られていない結果も無いときだけです。探索の中のPDFの解析・検索・検証・保存はスレッドで行い、イベントループを止めません。CLIの`ask`は終わるまで待ちます（spec v1.15、v1.26）。

引用検証の規則: 各事実には短い原文引用・PDFの物理ページ番号・セクション（nullならサーバーが導く。spec v1.27）を付けます。引用の存在、ページ、指定セクションはコードで検証します（`pdf.py`の`validate_evidence`、`EVIDENCE_INVALID`/`EVIDENCE_WRONG_SECTION`）。研究方法・データソース欄の引用が背景・参考文献・目次・ENCePPチェックリストにしか存在しない場合は`EVIDENCE_WRONG_SECTION`で保存を拒否します（補足事項・質問別回答はその章自体への質問もあるため許容）。データソース名は、名前の語がその順に引用のどれかに書かれていれば通します（大文字と小文字、記号の違いは無視し、語の間に他の語が3つまで入ってよい。ただし`;`、文末、名前自身の語をまたぐ並びは通しません）。名前が引用に無く方法の本文にあれば、サーバーが名前の箇所を引用に足します（引用が8件ある事実と、切り出しが1,000字を超えるときは足しません）。この規則は呼出元抽出・サーバー側抽出・質問別回答のすべてに当てはまります（spec v1.12、v1.26）。逐語の引用が示したページに無く別のページにあれば、最も近いページに直します（等距離の2ページにあるときは拒否します）。ページの訂正も、呼出元抽出と質問別回答の両方に当てはまります（spec v1.13）。引用が存在することは、要約や使用区分の意味が正しいことの保証ではありません。

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
uv run ema-rwe analyze 1000000479 --refresh
uv run ema-rwe cleanup-cache
```

## 抽出経路の設定条件

`LLM_MODEL`があり、`LLM_BACKEND=litellm`または`LLM_BASE_URL`が設定されている場合だけサーバー側抽出を使います。APIキーだけでは有効になりません。その他は呼出元抽出です。

共通スキーマ・引用検証・DB再利用は両経路で使いますが、探索範囲や品質はモデルと読み方に依存します。サーバー側へ渡るのは指定した質問とPDF情報で、会話履歴全体は自動転送されません。既知の条件は質問に含めてください。費用・速度の過去実測は[検証記録](docs/validation.md)、今回のCodex Appの4例は[ベンチマーク記録](docs/benchmarks/20261009-codex-app.md)を参照してください。

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
| `EMA_CATALOGUE_UPDATE` | `apply` | GitHub Releases のカタログのスナップショットの取り込み。`apply`は起動時（1日1回まで）に確かめて検証後に取り込み、`check`は`catalogue_status`で知らせるだけ、`off`は確かめません。自動で更新するのは利用者のデータフォルダのDBだけで、`EMA_DB_PATH`のDBとcheckoutのDBは`ema-rwe update-catalogue`で更新します（spec v1.18） |
| `EMA_CATALOGUE_RELEASES_URL` | このリポジトリのリリース一覧 | スナップショットを探すGitHub APIのURL（別のリポジトリから配る場合） |
| `EMA_MAX_SCREENING_STUDIES` | `5`（1〜1000） | 一次判定でPDF取得・全件解析へ進める最大研究数 |
| `EMA_MAX_COMPARISON_STUDIES` | `5`（1〜1000） | 比較表へ掲載する最大研究数 |
| `EMA_MAX_LISTED_CANDIDATES` | `50`（1〜1000） | `needs_narrowing`時に`candidates`一覧を返す最大件数 |
| `EMA_USER_AGENT` | `ema-rwe-mcp/0.6.0` | EMAへのHTTPリクエストのUser-Agent |
| `EMA_RESEARCH_BUDGET_CHARS` | `40000` | 呼出元向けの追加探索応答の文字数予算 |
| `EMA_SEARCH_BUDGET_CHARS` | `20000` | 呼出元向けのPDF全文検索応答の文字数予算 |
| `EMA_PROTOCOL_DIR` | DBと同じ親フォルダ内の`protocols` | 保持するPDF/JSONの保存先（見出しの英訳はDBに置く。以前の版の`.headings`は取り込みにだけ使う） |
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
| `LLM_WAIT_SECONDS` | `120` | `analyze_protocol`が`status=extracting`を、`research_protocol`が`status=answering`を返すまでの待機秒数。クライアントのツールのタイムアウトより短くします |

## 内部LiteLLM経路の設定例

```powershell
uv sync --extra llm
```

| 接続先 | `LLM_MODEL` | 認証・追加設定 |
|---|---|---|
| Gemini | `gemini/<model-id>` | `LLM_API_KEY`または`GEMINI_API_KEY` |
| Anthropic | `anthropic/<model-id>` | `LLM_API_KEY`または`ANTHROPIC_API_KEY` |
| Azure OpenAI（v1 API、`.../openai/v1`） | `openai/<deployment-name>` | `LLM_API_KEY`、`LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1`。`LLM_API_VERSION`は空。reasoningモデルは`LLM_REASONING_EFFORT`、上限は`LLM_MAX_TOKENS` |
| Amazon Bedrock | `bedrock/<model-or-inference-profile-id>` | AWS認証チェーン（プロファイル、ロール、アクセスキー等）と`AWS_REGION_NAME`。IAM認証では`LLM_API_KEY`を設定しない |

Azure OpenAIの従来deployments APIやOpenAI直結の設定例、LiteLLMの接続仕様、内部探索のステップ上限・検証範囲は[docs/exploration.md](docs/exploration.md#litellm経由の内部探索)を参照してください。APIキーは共有する設定ファイルに直書きせず、起動元の環境変数で渡します。

## MCPツール一覧

stdioで18ツールを公開します。引数・範囲・既定値の正本は[`mcp/server.py`](src/ema_rwe/mcp/server.py)のツールスキーマです。完全な呼出手順とフィールドの意味は[docs/mcp-workflow.md](docs/mcp-workflow.md)を参照してください。

| 用途 | ツール |
|---|---|
| カタログ準備 | `catalogue_status`, `import_catalogue_csv`, `refresh_drug_dictionary` |
| 検索計画・候補確認 | `plan_study_search`, `search_studies`, `get_study` |
| 全候補screening・比較出力 | `compare_protocols`, `get_protocol_comparison` |
| PDF取得・保存済み版 | `get_protocol`, `list_local_protocols` |
| 共通抽出・見出し英訳 | `analyze_protocol`, `cache_protocol_analysis`, `cache_heading_translations` |
| PDF探索 | `get_protocol_outline`, `search_protocol_text`, `read_protocol_text` |
| 質問別回答 | `research_protocol`, `cache_protocol_answer` |

特に次の違いを維持してください。

- `compare_protocols`は全検索variantの重複除外unionを数え、`role`は順位付けにだけ使います。`search_studies`はpreview検索で、`role`が検索列を限定します。
- `filters`は国・catalogueデータソース種別・研究デザインだけです。`facets.conditions`で絞るときは追加のconditionブロックをANDで渡します。
- screening上限と比較表上限は独立です。上限以内の全`pending_tools`を処理・保存し、最終的に`get_protocol_comparison`で全screening研究の出力を更新します。
- `source_preference`はPDFの定義用途に対する優先条件です。既定は`prefer`、明示的な限定依頼だけ`only`を使い、未評価・不明の候補を優先条件だけで除外しません。
- 英語以外のPDFでは、抽出・探索の応答の`reading`を保存へ渡します。途中で読み方が変わった場合はやり直します。
- `compare_protocols`の`pending_tools`は、解析も回答も無い研究には質問付きの`analyze_protocol`を1つだけ出します（内部LLM設定時は`analyze_protocol`と`research_protocol`の2つ）。
- 全ツールに題名と注釈（読み取り専用・破壊的・冪等・外部通信）があります（`mcp/server.py`の`ANNOTATIONS`、割り当ては[spec v1.16](docs/spec/v1.16.md)。`cache_heading_translations`と`analyze_protocol`は[spec v1.26](docs/spec/v1.26.md)で破壊的に改めた）。ツールを足すときは、この表にも足します（無いと起動時に`KeyError`になります）。`compare_protocols`と、サーバー側抽出の`analyze_protocol`は、クライアントが求めたときに進捗を通知します（`compare_protocols`は最後に`(n, n)`を送ります）。通知の送信に失敗しても、stderrにwarningを記録してツールは続けます。
- 解析の`source`、質問別回答と比較の`versions`には、作成時のアプリの版・カタログのスナップショットの日付・parserの版が入ります（spec v1.19）。保存済みの結果を返すときも、作成時の値のままです。`catalogue_status`の`app_update_available`は、動いている版より新しいリリースがあるときだけ現れます。
- ツールのスキーマに無い引数名は検証エラーで断ります（`create_server`で引数モデルを`extra="forbid"`にし、`inputSchema`に`additionalProperties: false`を付けます。spec v1.28）。
- 引用の`server`はサーバーが付ける印です（spec v1.13）。`source_name`はデータソース名の書かれた箇所としてサーバーが足した引用（使い方や定義を支える引用ではありません）、`page`はサーバーがページを直した引用です。印の無い引用は、呼出元か内部LLMが渡したままです。呼出元や内部LLMが書いた`server`は、サーバーの処理の前に消します（`pdf.without_server_marks`、spec v1.26）。
- 検索の条件（DARWIN EU、状態、類義語、コード、絞り込み、役割、範囲など）は、`storage.SearchRequest`の1つの値で受け渡します。条件を変えた検索は`dataclasses.replace(request, role=...)`のように名前で作ります。`search_studies`と`compare_protocols`の任意の引数は名前でしか渡せず、MCPとCLIのアダプタはサービスを名前付きの引数で呼びます。
- 完了済み解析を`coverage_complete=true`で修正するときは全項目を渡します。一部だけの再保存は解析全体を置き換えます。統合の後にバッチの無いまま空の解析（`missing_information`だけのものを含む）で呼び直すと、保存済みの解析を残して`INCOMPLETE_EXTRACTION`で断ります（spec v1.22、v1.26）。

CLIのサブコマンドは、MCPのツールと1対1で対応し、同じ引数を持ちます（spec v1.17）。

| CLI | MCPツール | CLI | MCPツール |
|---|---|---|---|
| `catalogue-status` | `catalogue_status` | `local-protocols` | `list_local_protocols` |
| `import-download` | `import_catalogue_csv` | `outline` | `get_protocol_outline` |
| `refresh-drugs` | `refresh_drug_dictionary` | `pdf-search` | `search_protocol_text` |
| `plan-search` | `plan_study_search` | `pdf-read` | `read_protocol_text` |
| `search` | `search_studies` | `analyze` | `analyze_protocol` |
| `compare` | `compare_protocols` | `cache-analysis` | `cache_protocol_analysis` |
| `comparison` | `get_protocol_comparison` | `cache-headings` | `cache_heading_translations` |
| `study` | `get_study` | `ask` | `research_protocol` |
| `protocol` | `get_protocol` | `cache-answer` | `cache_protocol_answer` |

名前の違いは決まった形だけです。繰り返す引数はCLIでは単数（`--synonym`と`synonyms`）、ファイルで渡す本体は`input`、`filters`は`--country`・`--source-type`・`--study-design`、`source_preference`は`--prefer-source-type`・`--source-role`・`--source-mode`です。`import-csv`、`import-all`、`backfill-protocols`、`merge-observations`、`cleanup-cache`、`update-catalogue`はCLIだけの保守用です。対応の表と照合は`tests/test_cli_mcp_parity.py`にあり、引数・既定値・選択肢・範囲のどれかが片方だけ変わるとテストが落ちます。

CLIは`uv run ema-rwe <サブコマンド> --help`で確認できます（例：`pdf-search`, `ask`, `cache-headings`, `cache-answer`）。入力範囲はCoreが検査し、MCPとCLIに共通で適用します。

## ドキュメント索引

| ファイル | 内容 |
|---|---|
| [docs/spec/v0.1.md](docs/spec/v0.1.md)〜[v1.31.md](docs/spec/v1.31.md) | 段階的な追加要件の正本。対象変更の関連仕様を読む |
| [docs/mcp-workflow.md](docs/mcp-workflow.md) | 呼出元エージェント向けの完全な手順（英語） |
| [docs/search-logic.md](docs/search-logic.md) | 検索語の展開・医薬品照合・順位付け |
| [docs/benchmarks/20261009-codex-app.md](docs/benchmarks/20261009-codex-app.md) | Codex Appの4例とMCP実行時間の定義 |
| [docs/clinical-search.md](docs/clinical-search.md) | 日本語疾患名・薬剤名→英語・医療コードの展開、辞書の網羅性の限界 |
| [docs/comparisons.md](docs/comparisons.md) | 複数プロトコルの比較・絞込ワークフロー |
| [docs/source-types.md](docs/source-types.md) | PDF由来データタイプの判定・表示順序・公式分類との差異 |
| [docs/exploration.md](docs/exploration.md) | 同義語・章構造・追加探索ツール・LiteLLM接続例 |
| [docs/codex-setup.md](docs/codex-setup.md) | このチェックアウトのCodex MCP設定の解説 |
| [docs/csv-profile-20260912.md](docs/csv-profile-20260912.md) | 実CSVの構造・欠損率調査 |
| [docs/data-source-linkage-20260912.md](docs/data-source-linkage-20260912.md) | Data Sources CSV結合検証（調査履歴） |
| [docs/validation.md](docs/validation.md) | 実サイト検証・自動検証・実測コスト・未検証事項 |
| [docs/research/](docs/research/) | 個別調査記録（用語マイニング、配布方式比較、PDF読解コスト、医療用語体系の比較等） |

実サイト検証と既知の制限は[docs/validation.md](docs/validation.md)を参照してください。抽出の精度は、4つのプロトコルの人手で読んだ正解データ（組入れ・除外基準60項目、アウトカム定義40項目。spec v1.20〜v1.24）で採点しています（`scripts/extraction_eval.py grade`／`run`）。仕様の10〜20プロトコルによる人手精度評価は未実施で、プロトコルの数、題材（組入れ・除外基準とアウトカム以外）、読み手の種類の広がりは確かめていません。
