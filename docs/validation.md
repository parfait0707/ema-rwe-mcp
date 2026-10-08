# 検証記録（2026-09-12）

## 実サイト確認

- [指定された検索ページ](https://catalogues.ema.europa.eu/search?f%5B0%5D=content_type%3Adarwin_study)を識別可能なUser-Agentで確認。Study IDのリンクとStudies用CSV Exportリンクを確認。
- [robots.txt](https://catalogues.ema.europa.eu/robots.txt)の `/search/` 禁止に従い、Export配下はHTTPで自動取得しなかった。CSVは表示ブラウザまたは手動取得後に取り込む。実CSV原本の検証結果は後段に記録した。
- [DARWIN EU RR1 opioids / Study ID 1000000479](https://catalogues.ema.europa.eu/study/1000000479) → `/node/4380/administrative-details`。Methodological aspectsでNon-interventional studyを確認。
- Data managementのData source typesは `Administrative healthcare records (e.g., claims)`、`Biobank`、`Electronic healthcare records (EHR)`。カタログ登録データソース名8件を取得。
- Study documentsのInitial protocolから [V3 PDF](https://catalogues.ema.europa.eu/system/files/2025-03/DARWIN%20EU%20Protocol_P3-C2-002_DUS%20Opioids_V3.pdf) を取得。45ページのnative textを抽出し、ページを保持して関連セクションを返却できた。
- [ESCORT-HU Extension / Study ID 1000000066](https://catalogues.ema.europa.eu/study/1000000066) でInitial V1、Updated V2、Updated V4を確認。V2の公開日2026-08-26はV4の公開日2026-04-14より後であり、公開日のみの最新版判定が誤る実例。切り出したProtocolカードを固定テストに使用し、V4選択を検証。

PDF全文や研究ページ全体をテスト用データとして配布しない。固定fixtureはProtocolカード断片のみで、連絡先を含まない。

## 自動検証

テストは固定fixture／Mock HTTPによる再現可能な検証。実サイトをCIや通常テストから繰り返し取得しない。

v0.1時点で`pytest`全46件、`ruff check src tests`、sdistとwheelのビルドが成功。

## v0.2追加検証

`pytest`全67件、`ruff check src tests`、v0.2のsdist／wheelビルドが成功。

- 同義語の英米綴り・日本語展開、複数語をフレーズとして検索、曖昧な略語の非展開。
- 目次・参考文献・ENCePPチェックリストの分離、親章・前後関係、章番号逆行の警告。参考文献の真正な引用でも当該研究の方法として保存する場合は拒否。
- PDFをID付きで保持し、HTTPキャッシュ削除後・Service再作成後もローカル検索／読取り可能。同一URLの新内容は別IDとなり、旧版も閲覧可能。
- MCP実stdioで追加探索、テキスト読取り、質問別回答の保存と再利用。
- LiteLLMの5プロバイダ名の引数ルーティング、実SDKのmock completion、内部探索の検索→読取り→回答保存、許可外アクション拒否とステップ上限。
- 実Study 1000000479の45ページPDFを保存済みIDで追加検索。欠測値に関するヒットをpage 31のQuality controlとpage 43のENCePPチェックリストとして区別でき、追加入力時のEMA通信は0件。

プロバイダの実API認証と実モデルの回答精度は未検証。`docs/exploration.md` に設定手順と制約を記載。

- 対象範囲: Non-interventionalのみ、DARWINフラグ独立、欠落した種別を採用しない。
- CSV: BOM、複数値、列不一致、upsert、原本SHA256、連絡先専用列を非索引化。
- 検索: FTSの入力エスケープ、status/DARWIN絞込、データソース欄必須、解析後の疾患コード検索、通信ゼロ。
- Protocol: 初版／更新版、数値版比較、日付、レポート除外、ページ・セクション・引用検証。
- 再利用: 解析の保存、再解析時のキャッシュ利用、同じURLでPDFが変わった場合の失効。
- HTTP: robots、リダイレクト許可先、429、Retry-After、TTLと削除。
- MCP: 実stdio subprocessでinitialize、list_tools、search_studies、入力検証、構造化エラー。

## v0.3 医薬品名・コード・Codex設定

- 全100テストが成功。ruff check／formatチェック、ロックファイル整合確認、v0.3.0のsdist／wheelビルドが成功。
- 商品名→一般名、一般名→商品名、ATC→医薬品名、PDFのINN／ATC単独記載、研究FTS、ICD-10形式と国別修正版の分離、ATC階層形式、配合剤と単剤の区別、失敗時の旧辞書保持を検証。
- 2026-09-12に公式EMA JSON（公開時刻2026-09-11T18:02:08Z、全2,734件）を取得し、名称・INN/common nameを持つヒト用医薬品2,244件を検索用に読み込み。原本SHA256: `29c9b83916515eee4cbbc21fc321e07bf6cfa6c27c063955becb0dcfdb3b724b`。
- `.codex/config.toml`を`codex mcp get ema-rwe --json`で確認し、enabled=true。同じcommand／args／cwd／envから実stdio接続し13ツールを確認。Eliquis、apixaban、B01AF02、J84.9の検索計画と医薬品辞書のキャッシュ再利用（追加通信0件）が成功。
- 医薬品名データはEMAの中央審査品目。世界各国・日本の全商品名は未網羅。辞書にない日本語薬剤名を実LLMが正しく翻訳する精度評価は未実施。

## 臨床概念検索の先行検証

臨床概念・コード検索の追加後は全81テスト、ruff、sdist／wheelビルドが成功。
日本語ILD問い合わせからコードのみのPDF本文・カタログ情報への検索、J84.9／J849の表記揺れ、J84.90や単独数字の誤検出防止、ICD-9-CM・ATC・数値コードの保持、独自辞書、LLM候補の未確認表示、MCP実stdioでの型付きコード指定を検証しました。医学的な対応関係の網羅性や実モデルによるコード提案の精度評価は含みません。

## v0.4 複数プロトコル比較・クライアント設定

- 全117テストが成功。ruff check／format、git diffの空白チェック、uvロック整合確認、v0.4.0のsdist／wheelビルドが成功。ローカルvenvもv0.4.0へ更新。
- 0／1／5／6／25候補、表示limitより前の総件数、検索語の統合・重複除去、6件以上でPDF通信・比較ファイル作成を行わないことを検証。
- 国・claims／registry／EHR／drug dispensing-prescription・5種の研究デザインの絞込、同一欄OR／別欄AND、不明メタデータ件数、CSV・研究ページからのデザイン取込、旧JSONの読込みを検証。
- 5研究の合成PDFについて、全件のPDFと下書きJSONを保存し、引用付き共通抽出・質問別回答を順に保存。一部未処理ならincomplete、全件完了後は再起動したServiceでcompleteとなり、全JSONと比較表が一致することを検証。
- プロトコルのない研究をエラー行・JSONとして保持し、後続研究も処理。PDF改変・解析fingerprintの変更を検出し、過去の完了状態や別版の解析を混ぜないことを検証。
- 内部LLMをmockに置き換え、呼出元方式と同じ比較完了条件・保存済み結果の再利用を確認。実APIへの接続試験ではない。
- 実stdio subprocessで15ツールを発見し、compare_protocolsの6件ゲート、search_studiesの構造化フィルタ、無効フィルタ／比較IDの検証を実行。
- Codexのプロジェクト設定を`codex mcp get ema-rwe --json`で確認しenabled=true。Claude Codeの`claude mcp add --help`と公式ガイドでREADMEの登録引数を確認。Claude Codeへの実登録・対話E2Eはこの変更では未実施。

## v0.5 Playwrightによる公式CSV取得支援

- Microsoft公式Playwright MCP 0.0.80をNode.js 24.1.0／npx 11.12.1からstdio起動し、24ブラウザツールを発見。Microsoft EdgeでEMA Supportページへ遷移できた。現在のStudies出力先は`data/imports/studies`。
- 表示ブラウザでEMAのStudies検索ページを1回開き、アクセシビリティsnapshotから`Export results`リンクをrole/nameで特定した。固定CSS selectorには依存しない。
- `Export results`を1回実行すると`/batch?id=...&op=start`へ移動し、27秒後に3%、残り約17分と表示された。長時間の実Export完了と実CSVの取込はこの検証では待たず、重複するExportも開始しなかった。
- CSV未登録／current／staleの判定、専用inboxからの取込、50 MiB・basename・拡張子・必須列の制約、検索結果の次アクション、実stdioで17ツールの公開と安全なパス拒否をオフラインテストした。
- Playwright MCPは別プロセスであり、rwd-catalogue-mcpから直接呼ばない。Codex／Claude Codeが状態確認、表示ブラウザ、取込、検索再実行を順に調整する。ブラウザによる定期同期や検索結果クロールは未実装。

## 公式Studies CSVの実ファイル検証

- 2026-09-12にユーザーが公式画面から取得した`20260912_export-data.csv`（SHA256 `2332a0375564e43eee7d3fe4eec2c8a50a2bc7bf1e1d730fa81fdb2db4354b3e`）を検証。原本は13,443,910 bytes、UTF-8 BOM付き、116列、3,312行で、全行が明示的なNon-interventional studyだった。
- Study ID、Title、Study typeは全件欠損なし。Study IDは全件数値かつ一意で、全行の列数は116だった。
- Python `csv.Sniffer`は実データの二重引用符規則を誤判定し、修正前のimporterは14レコード目で列ずれとして停止した。区切り文字だけを推定しRFC 4180の引用規則を固定した後、一時SQLiteへ3,312件を全件取り込めた。
- 実CSVの`Title`、`Data source(s)`、`Other linked data sources`、自由記述designに対応。ATC、INN/common name、疾患、outcome、目的、population等を連絡先と分離した補助FTSへ取り込み、実データで`B01AF02`を検索できた。
- Studies CSVにはStudy側F8.7 Data sources (types)列がない。取込結果は警告を返し、選択研究のdetail pageで取得する。Data Sources exportのC5.1は資産分類なので、F8.7の代用にはしない。
- 集計値と欠損率は[`csv-profile-20260912.md`](csv-profile-20260912.md)を参照。原本CSVと連絡先値はGit管理・検証ログへ含めない。

## Human Data Sources CSVとの結合検証

- `20260912_rwd-catalogues-data-source-human-export-public.csv`は131列、286件。Data source ID・名称は全件欠損なし、一意で、Data source typeは285件に存在した。
- Studies CSVの`Data source(s)`にある2,004参照を正規化名称で照合し、857 Studyの全参照がData Sources CSVへ一意に一致した。曖昧一致と未一致は0件。
- C5.1公式type`Administrative healthcare records (e.g., claims)`を持つ42 Data Sourceから、329 Studyをclaims利用可能資産へのリンク候補として抽出できた。Study側F8.7のclaims 831件とは分母・意味が異なり、329件をclaims使用Studyとは断定しない。
- `Other linked data sources`は自由記述1,174断片のうち名称25件、acronym 47件だけが一意に一致した。主結合とは分けて扱う。
- 詳細は[`data-source-linkage-20260912.md`](data-source-linkage-20260912.md)。調査当時は原本を`data/imports/data-sources`へ保管した。2026-09-13のユーザー依頼でこのフォルダと元CSVを削除済み。Studies原本は`data/imports/studies`に保持し、Git管理しない。

## PDF由来の用途別データタイプ・独立上限（v0.6、2026-09-13）

- `.venv/Scripts/python -m pytest -q`: **141 passed**（16.82秒）。実stdioのMCP発見・新引数の検証・不正引用の拒否を含む。
- `ruff check src tests`と`ruff format --check src tests`成功。`git diff --check`成功。
- `python -m build`で0.6.0のsdist／wheelを生成。`uv lock --locked --offline`で78パッケージのロック整合性を確認。
- 合成PDFとモックHTTP／LLMで、caller-assisted保存と内部LLMの両経路、source_assessmentsの引用・ページ・ソース名検証、希望タイプ変更時の再利用を検証。実際のLLMによる分類精度を測定したテストではない。
- 希望タイプ、用途違い、planned／candidate／unclear、連結依存、推定、他タイプ、未判定、矛盾、一般抽出だけでは質問への適合を確定しないことを検証。
- 一次判定3／比較2の独立設定、全PDF／JSONの保持、明示的なID選択、不正・重複・過大な選択の拒否、設定変更後も既存比較の上限が変わらないことを検証。一次判定上限を6にすると6PDFを保存し、既定の表示5件へ自動切捨てしないことも確認。
- 公式type未判定6件へ希望typeを指定しても、既定の5件ゲートをすり抜けずPDF通信しないことを検証。
- DB schema 2→3、旧解析の履歴退避、確認日時だけの更新で履歴が増えないこと、type列のないStudies CSV再登録で公式分類・由来・確認日時・解析が維持されることを検証。
- `data/imports/data-sources`のCSV1ファイルと空ディレクトリを個別削除し、フォルダが存在しないことを確認。Studies CSV（13,443,910 bytes）は残存。再帰削除は自動承認レビューで拒否されたため使用しなかった。
- Data Sources CSVの必須取込・名称結合は導入していない。公式Study分類とプロトコルの用途別分類を別々に保持する。[仕様](source-types.md)。

## 2026-09-14 種別タグ付きカタログDBの構築

- `data/imports/studies/20260913_all_export-data.csv`（Non-interventional全件）と`data/imports/source_type/20260913_{claims,ehr,registry}_export-data.csv`を`uv run ema-rwe import-all`で取り込み、`data/ema.sqlite3`（約36.8 MB、WALチェックポイント済み）を構築してコミット。CSV本体はGit管理外。
- 取込結果: studies 3,312件、claims 819件、ehr 914件、registry 536件（すべてNon-interventional）。種別exportの和集合1,799件は全件の部分集合で、タグなし（others）1,513件。3種別すべてを持つ研究77件。
- 全件exportにData source type列がないことを確認（`schema_warnings`）。種別exportも同じ列構成で、種別はファイル名にのみ含まれる。
- CLIスモーク: `search "type 2 diabetes SGLT2" --all-studies`は1,947件で`needs_narrowing`、`next_action`が種別と実施国の質問を指示。`--source-type registry --country Denmark`で89件に減少。通信なし。
- `uv run pytest -q`: 148 passed（種別タグ取込・ファイル名解析・others絞り込み・既定DBパスの回帰テストを追加）。`ruff check`／`ruff format --check`とも合格。
- 未検証: Windows側`.codex/config.toml`の絶対パスでの実起動、MCPクライアントからの`needs_narrowing`→質問→`filters`再実行の実会話。

## 2026-09-15 未収録語のログ

- 日本語を含み、概念辞書・語彙グループ・医薬品辞書のいずれにも一致しない質問・検索語を `data/terminology_unmatched.json` に記録。`plan_study_search`、`search_studies`、`compare_protocols` の 3 経路から同じ関数で記録し、`catalogue_status.unmatched_terms` に上位 20 件を返す。
- テスト `tests/test_unmatched.py`: 未収録の日本語は記録・件数加算、収録済み（肝障害）と英語は記録しない、壊れたログファイルは無視して再作成、`catalogue_status` に反映。

## 2026-09-14 関連語のカタログ語彙からの導出（Sonnet 作業／Opus 検証の 5 巡）

- `scripts/mine_terminology.py`: 各研究のタイトル・Outcomes・Medicinal condition・目的から 1〜3 語の n-gram を抽出し、`english_terms` で定義したアンカー研究とそれ以外の出現率をラプラス平滑化した対数オッズ比で比較。閾値は `a>=3`、`a/A>=0.03`、対数オッズ比 `>=log 8`、本番 FTS（NEAR）で新規に一致する研究 `b_new>=1`、上位 60 件。同一トークンを繰り返す n-gram は除外。
- 採否規則: 同じ臨床実体・直接の発現・測定値・下位型のみ採用。曝露薬・適応症・薬効群・処置・母集団・デザイン語・別概念・疾患横断の一般指標は不採用。3 文字以下の略語は禁止、4〜5 文字は新規一致研究のタイトル確認を必須。採用前に新規一致研究のタイトル最大 10 件を確認し、無関係が 30% 超（確認 3 件以下なら 1 件でも）なら不採用。この標本確認は採用語全件に適用した。`--apply` が候補由来・略語・概念境界・上限を機械検査する。順列重複の n-gram は 1 代表に畳み込む。
- 結果: 候補 2,839 件から採用 19 語（11 概念）。検索件数の増分は概念あたり 0〜7 件で、`b_new` の合計と 48 概念中 44 概念で完全一致（残り 4 概念は採用語の一致集合の重複、または本番 `expand()` のコード・日本語展開ぶんで差分が下回る）。独立検証で誤検出率 30% 超を実測した第 3〜4 巡の採用語 19 件は除外した。
- 再現性: HEAD の辞書を入力に再実行すると `generated_at` 以外バイト一致。`--apply` が作業ツリーの辞書をバイト単位で再生する。
- テスト: `tests/test_terminology_mining.py` 30 件（3 閾値の境界、`b_new==0` 除外、`--apply` の不変条件・機械検査、重複トークン除外、順列畳み込み、`reason_codes`）。全体 187 passed、ruff 合格。判定記録 `data/terminology_decisions.json`（約 0.33 MB）はコミット対象。
- 限界: `thromboembolic events`（VTE）のように新規一致は多いが対数オッズ比が低い一般語は上位 60 件に届かない。`b_new` を加味した順位づけは未導入。`haemorrhage`／`bleeding` が英語名にある 3 概念は互いの研究を共有する。

## 2026-09-14 近傍一致・役割別検索・候補一覧・辞書同梱

- DBをスキーマv4で再構築（`uv run ema-rwe import-all`、3,312件、種別タグ1,799件）。役割列の充足率はCSV実測でOutcomes 81%、Medicinal condition 78%、INN 41%、ATC 46%、Main study objective 98%。
- 実DBの件数: 「liver injury」旧OR一致1,999件→NEAR 50件→同梱辞書の関連語込み103件、`--role outcome`92件。「肝障害」（日本語）103件、`--role outcome --source-type claims --country Japan`で1件（46425）。この1件は実走でPDFから肝障害定義を確認した研究と一致。
- `search_studies`応答サイズ: 5件表示で約22 KB（`detail=full`）→約10 KB（既定compact）。
- 医薬品辞書: `refresh-drugs --force`で2,244レコード（公式2,734件中、人用）を取得し`data/ema-medicines.json`（約6.7 MB）としてコミット。
- `uv run pytest -q`: 157 passed（`tests/test_screening.py`を追加: NEAR、役割列、compact/full、候補一覧上限、conditions facet、`study_ids`、既定辞書、役割列の取込）。`ruff check`／`ruff format --check`合格。stdio MCPテスト（`tests/test_mcp.py`）合格。
- 未検証: NEARの距離3は経験則で、離れた語順の表記を見逃す可能性がある。辞書のコードはWHO ICD-10 2019の分類見出しに限定し、人手確認は未実施（`verification=unverified`）。

## 2026-09-16 clone不要の配布（wheelへのデータ同梱）

- `pyproject.toml`の`force-include`で`data/ema.sqlite3`・`ema-medicines.json`・`terminology.json`をwheelの`ema_rwe/data/`へ同梱（wheel約13.6 MB圧縮、展開後約50 MB）。`config.default_data_dir()`はcheckout外ではユーザーデータディレクトリへ同梱データを初回複製し、既存ファイルは上書きしない。
- 実検証: `uv build --wheel`で0.7.0のwheelを生成し、checkout外の使い捨てvenvへ導入。`PYTHONPATH`（devcontainerが`/workspace/src`を設定）を外し、`XDG_DATA_HOME`を一時ディレクトリに向けて`ema-rwe catalogue-status`（status current、snapshot 4件）と`ema-rwe search diabetes --all-studies`（310件、needs_narrowing）が動作。複製先に3ファイルが生成されたことを確認。
- `uv run pytest -q`: 192 passed, 1 skipped（`tests/test_distribution.py`を追加: force-include網羅、checkout時の既定パス、wheel導入時の初回複製と非上書き）。`ruff check`／`ruff format --check`合格。`uv lock --check`合格。
- 未検証: `uvx --from git+https://github.com/parfait0707/rwd-catalogue-mcp ema-rwe-mcp`の実行はリポジトリが非公開のため未実施（ローカルwheel導入で同等経路を検証）。Windowsの`%LOCALAPPDATA%`への複製は未実施。

## 2026-09-24 サーバー側抽出（Azure OpenAI）と呼出元抽出の圧縮対策

- 接続: Azure OpenAI v1 エンドポイント（`/openai/v1`）は OpenAI 互換のため `LLM_MODEL=openai/<deployment>` + Bearer で疎通。`reasoning_effort=high`、`max_completion_tokens` 上限 128,000（エラー応答から取得）。`azure/` プロバイダ指定は 404。
- 単体の抽出（gpt-5.6, effort=high）: 49733（67 頁、関連 94k 字）1 バッチ 116 秒。19786（242 頁、488k 字）2 バッチ並列 116 秒、`status=extracting` を 1 回返してから完了。証拠の脱落は 49733 で 94 件中 29 件（省略引用 19、ページずれ 6→自動修復、データソース名 11→窓引用で一部修復）。
- q1「日本のレセプトでの膵炎アウトカム定義」を headless Claude Code（`claude -p`、`--strict-mcp-config`、ファイル系ツール禁止）で実行:

| モード | turn | 所要 | コスト | 結果 |
|---|---|---|---|---|
| 呼出元抽出（改修前、9/24 05:20） | 98〜113 | 24 分 | $22〜30 | 利用制限で中断、回答なし |
| サーバー側抽出（修正前コード） | 66 | 42 分 | $8.3 | 完走。`research_protocol` の内部探索が全件 `exploration_limit_reached` |
| サーバー側抽出（修正後） | 37 | 22 分 | $6.1 | 完走。`research_protocol` は全文一括回答 |
| フォールバック（LLM 未設定、Sonnet サブエージェントへ委譲） | 5（メイン） | 約 30 分 | $22.0 | 完走。サブエージェント 14 本（うち 4 本が研究担当、`batch_offset` で途中保存） |

- 修正で直した不具合: (a) 抽出後の最終検証で `EVIDENCE_INVALID`（section 不一致）— データソース名の窓引用を足す修復で、落ちた元の引用を残していた。修復後の証拠一式で再検証するよう変更。(b) 内部探索の 8 ステップが 1.2 万字の読み取りで尽きる — 関連セクション全体を一括で渡す方式へ変更。
- `uv run pytest -q`: 206 passed。`ruff check` / `ruff format --check` 合格。
- headless 実行の注意: サブエージェントを待つ `claude -p` は既定 600 秒で打ち切られる。`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` で回避。2 本同時実行はセッション利用制限に達しやすい。
- 盲検採点（q1、n=1、両出力を一次資料と照合）: サーバー側抽出モード優位、確信度は中。フォールバック側に件数表記の誤りと未処理研究の開示漏れがあった。報告は `docs/agent_report/202609242030_blind_judge_q1_provider_vs_fallback.md`（Git 管理外）。
- 出力量の制御（2026-09-24 夜）: 抽出プロンプトを「節ラベルは null（サーバーが引用位置から導出）、value/definition は電報体、JSON は最小化、事実・コード・条件の省略禁止」に変更。19786（effort=high）の可視出力 61,858→34,469 字（-40%）、出力トークン 29.3k→22.9k（-22%、reasoning 分は不変）、バッチ 1 の所要 116→84 秒。検証後の証拠件数は 145→186、49733 は 92→92 で減少なし。主要アウトカム定義のコード・時間窓・感度特異度は保持。節ラベルは 65 件中 59 件をサーバーが補完。
- effort 比較（19786 / 49733）: medium は所要 44 秒（high の約 2.5 分の 1）だが証拠件数が 113 / 50（high 145 / 92）に減る。reasoning トークンは high 4〜6k、medium 0.3k で、差は書き出し量。抽出は high を維持。
- retry の確認: LiteLLM は `num_retries=0`、内部の OpenAI SDK は既定 2 回。SDK ロガーを DEBUG にした 19786 抽出で `Retrying request` 0 件、所要 127 秒（再試行なしの所要と一致）。MCP ログの `429` はタイムスタンプの部分一致。
- モデル上限: gpt-5.6 Luna は文脈 1,050,000 / 最大出力 128,000 トークン。`LLM_MAX_TOKENS=128000` は上限値そのもの（実測の最大出力は 17k）。`LLM_BATCH_CHARS=300000`（約 105k トークン）は 272k トークンの課金段階と 128k 超での精度低下報告を避ける設定。
- 未検証: q2〜q6 は未実行。Codex 側の `tool_timeout_sec=180` に対し `research_protocol` の一括回答（約 2 分）はポーリング化していない。

## 2026-09-25 応答圧縮（提案 1〜4）の A/B（q1、各モード 1 ラン、判断待ち）

ブランチ `feature/compact-responses`（コミット cd41f3e）。変更: `research_protocol` 鍵なし応答を質問関連順・予算付きのセクション束に、
`analyze_protocol` はスキーマ・指示文を初回バッチのみ・`max_chars` 上限 150,000・既定は要約（`detail=full` で本体）、
`search_protocol_text` に `max_chars` 予算、`get_protocol_outline` は既定 compact、比較応答は行本体を省略（`detail=full` で全体）。
交絡: 旧ランは 9/24 19:16・19:38（電報体プロンプトのコミット 22:32 より前）で、新ランはプロンプト変更も含む。

| 指標 | サーバー側抽出 旧 → 新 | フォールバック 旧 → 新 |
|---|---|---|
| メイン turn | 37 → 32 | 5 → 15 |
| 壁時計 | 22 分 → 17 分 | 36 分 → 28 分 |
| Claude 側コスト | $6.10 → $4.00 | $21.97 → $22.04 |
| 入力トークン（cache 含む） | 127 万 → 79 万 | 68 万 → 85 万（サブエージェント除く） |
| ツール結果字数（サブエージェント含む） | 602k → 313k | 3,251k → 2,569k |
| 一次判定で処理した研究 | 6 → 6 | 4 → 8 |
| 保存された抽出の証拠件数（19786 / 20765 / 49733） | 150→201 / 81→139 / 87→97 | 118→117 / −→155 / 50→76 |

盲検採点（`docs/agent_report/202609251145_blind_judge_compact_ab.md`、Git 管理外）: サーバー側抽出モードは**旧優位（確信度 中〜高）**、
フォールバックは**新優位（確信度 高）**。サーバー側抽出モードの新ランには検索候補を事実として断定した誤り 1 件と、カタログ／PDF 不一致の
未警告 1 件があり、採点者は圧縮起因か実行のばらつきかを 1 ランでは判別できないとしている。フォールバック旧ランには捏造された定義行 1 件と
上限 5 件で SAFEGUARD 未処理があった。全 4 応答共通で `catalogue_status` の件数を registry export の 536 件と誤読しており、ツール側の要修正点。

### q2（心不全患者コホート定義、2026-09-25、旧コード=main を別 worktree で実行し、両方とも電報体プロンプト）

| 指標 | サーバー側抽出 旧 → 新 | フォールバック 旧 → 新 |
|---|---|---|
| 壁時計 | 19 分 → 18 分 | 40 分 → 32 分 |
| Claude 側コスト | $8.61 → $5.05 | $30.17 → $24.22 |
| 入力トークン（メイン、cache 含む） | 283 万 → 134 万 | 321 万 → （headless 出力の末尾が通知文に置換され計測不能） |
| ツール結果字数（サブエージェント含む） | 503k → 405k | 2,751k → 2,011k |
| 一次判定で処理した研究 | 5 → 4（共通 2 件） | 5 → 7 |
| サブエージェントの `max_chars` 120,000 使用 | — | 0 回 → 7 回 |

盲検採点: サーバー側抽出は**新がわずかに優位（確信度 中）**、フォールバックは**同等（確信度 中）**。両モードとも一次資料照合で誤り・捏造ゼロ。
採点者の申し送り: 旧新で一次判定に選ぶ研究が異なるため「探索軌跡込みの比較」になっており、圧縮の影響だけを見るなら `study_ids` を固定した実行が必要。
PDF 取得先ディレクトリ名から X/Y が推測可能だった（P）、回答本文が内部パスを露出した（F）ため盲検は不完全。
フォールバック新ランは headless 出力の `result` が最終回答ではなく末尾の通知文になっていたため、記録から本来の回答（3,042 字）を復元して採点した。

## 2026-09-25 しおり（ブックマーク）による読取範囲の絞り込みと抽出スキーマ v0.3（cohort）

- `pdf.py`: PDF のしおりを各ページに付け、見出し検出で役割が決まらない章にしおりの題名から役割を与える。`reading_order` は本文の章＋本文が参照する付録＋コードリスト題名の付録だけを読む（チェックリスト・履歴書は除外）。しおりのない PDF は従来どおり関連セクション全読。質問別探索はしおりの章名一致を最優先。
- 読取量（手元 17 本）: しおりあり 10 本。19786（242 頁）は 456,378 → 103,647 字（-77%）。他は付録参照の有無でほぼ不変（49003 134k→134k、50641 239k→237k、40994 27.5k→21.3k）。19786 で旧抽出が引用していた証拠ページはすべて新しい読取範囲内。
- スキーマ v0.3: `cohort`（`inclusion_criteria[]`, `exclusion_criteria[]`, `index_date`, `baseline_period`, `follow_up`, `design_schema{figure_pages, time_windows[]}`）。fingerprint の schema を 0.3 に更新（既存キャッシュは再抽出）。統合・検証・除去はネストしたブロックを再帰処理。比較表に「コホート定義」「設計図（ページ・時間窓）」列を追加。
- 実抽出（gpt-5.6, effort=high, 1 バッチ）: 19786 は 87 秒で組入 7・除外 6・インデックス日・ベースライン・追跡・設計図ページ [17, 18, 42, 43]・時間窓 6。49733 は 104 秒で組入 7・除外 6・時間窓 6（設計図ページなし）。時間窓の例: 「look-back 6 months pre-index including index date」(p. 22)。
- 見つけて直した不具合: `drop_invalid_evidence` がネストした辞書を事実として扱い `cohort` ごと捨てていた（最初の実行で cohort が null）。
- `uv run pytest -q`: 215 passed。`ruff check` / `ruff format --check` 合格。
- 未検証: しおりのない PDF での読取量削減は行っていない（設計どおり）。画像の設計図は読まない（本文の時間窓記述のみ）。

## 2026-09-26 実プロトコルでの抽出検証（romosozumab PASS、研究 1000000308、日本 MID-NET）

- 対象: ユーザー指定の Original（2019-02-06、50 頁）と、MCP が既定で選ぶ Amendment 2（同日付）。Original は `version` メタデータが無く API から選べないため、同じ HTTP クライアントで取得して同じ抽出パイプラインを直接適用した。
- 所要: Original 抽出 91 秒（読取 7.7 万字、除去 8 件）。MCP 経路は抽出 77 秒、質問別回答 52〜69 秒。
- 一次資料照合（`docs/agent_report/202609260030_verifier_romosozumab.md`、Git 管理外）: コホート定義 **一致**（3 治療群の新規開始、50 歳以上、2019-03-04 以降、除外は開始前 365 日未満、インデックス日、ベースライン 365 日、打ち切り 5 事由、マッチング不適用。「骨粗しょう症の診断コード要件は存在しない」点も両経路が明示）。Study Design **一致**（後ろ向き新規使用者・実薬比較コホート、曝露定義、追跡、傾向スコア解析。Amendment 2 のみの IPCW を MCP 経路が取り込み）。Serious Cardiovascular Events 定義 **部分一致**（複合の構成 3 イベント・検証済みレセプトアルゴリズム・初回入院日・診断カテゴリは一致。具体コード表 Table 2 は一次資料で CCI 黒塗り。Abstract の「Two types of outcomes」と 8.7.2.4 見出しに黒塗りの第 3 区分があることを両経路とも欠落情報に記していない。証拠 1 件の節ラベル誤帰属）。引用ページは 105 件すべて物理ページと一致。
- 改善指摘（優先順）: 黒塗り区分の数の不一致を欠落情報に構造化して出す／MCP 経路で `data_sources` が 3 件（NDB・MDV・DeSC、本文前半章）欠落／値中の固有名が引用に逐語で出現する検証／節ラベルの帰属検証／`requires_linkage` の判定基準の明文化／日本語回答での数式的定義は原文語を併記／Original 直接抽出でも除去件数を成果物に残す。

## 2026-09-26 過剰実装の削減と試行例ハードコードの汎用化

- 削減: アトミック書き込み 4 実装を `domain.atomic_write` に統一、恒等関数 `preference_for`、CLI の `serve`（`ema-rwe-mcp` と重複）、未使用引数、重複条件、分割処理・非手法ロール集合・スキーマ版の重複を除去。Document.kind から生成されない `"protocol"` を削除。
- 汎用化: ILD 概念をコードから `data/terminology.json`（49 概念）へ移動し、コード候補は他概念と同じく `local_dictionary`/`unverified`。Eliquis の 1 件フォールバックと エリキュース／アピキサバン の特例を削除し、辞書の概念に一致した英語名を医薬品辞書へ渡す汎用経路に置換（辞書が無いときは何も展開せず `needs_refresh=true`）。同義語グループから試行例由来の抗凝固薬・DOAC・オピオイドと辞書と重複する心房細動・妊娠を外し、研究デザイン・手法・集団の頻出語（交絡、インデックス日、症例対照、実薬対照、不死時間バイアス、リンケージ、小児など）を追加。国名の別名を日本・米国・英国の 3 か国から、同梱カタログで研究数の多い上位 30 か国の日本語名・英語別称へ拡張。
- 検証: `uv run pytest -q` **235 passed**（実 stdio MCP テストを含む）、`uv run ruff check` / `ruff format --check` 通過。新規テストで日本語手法語の展開、薬効群を同義語グループで展開しないこと、国名別名、日本語薬剤名の辞書経路、医薬品辞書不在時の非展開を確認。
- 未検証: 同義語グループの追加語が PDF の関連章選択（`sections()` の `relevant`）を広げる影響を実プロトコルで測っていない。既存の `uvx` 利用者のユーザーデータ領域の `terminology.json` は初回シード後に上書きされないため、ILD 概念を得るには削除して再シードするか `EMA_TERMINOLOGY_PATH` で同梱版を指す必要がある。

## 2026-09-26 プロンプト監査の修正（MCP 契約・開発手順）

- `search_studies` の `darwin_only` 既定値を `false` に変更し、MCP `instructions`・`docs/mcp-workflow.md`・`compare_protocols` と一致させた（CLI とサービス層の既定値は変更していない）。`docs/spec/v0.1.md` の既定値の記載も更新。
- 説明の無かったツール引数（`synonyms`、`codes`、`filters`、`version`、`refresh`、`force_refresh`、`fingerprint`、`use_llm`、`column_map`）に 1 句の説明を追加。docstring は変更していない。
- OpenAI 互換経路の出力上限を固定 6000 から `max_output_tokens()`（LiteLLM 経路と同じ）に揃えた。
- `AGENTS.md` の開発コマンドを Windows の `.venv/Scripts/python` から `uv run` に変更し、PreToolUse フックと `.claude/rules/package-management.md` に一致させた。
- 検証: `uv run pytest -q` **235 passed**（実 stdio MCP テストで `darwin_only` 既定値 `false` と引数説明の公開を確認）、`uv run ruff check` / `ruff format --check` 通過、`uv build --help` で `uv build` の存在を確認。
- 未検証: 互換経路で出力上限を引き上げた際の実プロバイダでの挙動（上限値を受け付けない端点がありうる）。Windows ホストでの `uv run` 実行。

## 2026-09-26 一致語の出所と類縁概念フォールバック（spec v0.5）

- 辞書分割：21 概念の別概念語を`analogous_terms`へ移した（relation 付き）。移行は機械的な移動のみで、語の追加・削除はない。
- 同梱カタログでの候補数（main → 本版、`role=any`）：1型糖尿病 310→50、2型糖尿病 316→176、乳がん 637→84、消化管出血 259→42、アナフィラキシー 147→54、急性腎障害 87→44、肝障害 181→132、糖尿病 310→310（不変）。main の「1型糖尿病」は「糖尿病」と同じ 310 件で、広義語グループの誤発火が主因だった。全表は[spec v0.5](spec/v0.5.md)。
- 出所付与の所要：展開語ごとに FTS を 1 回ずつ引くが、同梱カタログで 1 検索あたり約 0.01〜0.02 秒。
- テスト：`tests/test_analogous.py` 15 件（出所、広義語抑止、0 件時のフォールバック、類縁スコープの除外とラベル、未知概念、比較表の表示、CLI 引数）。実 stdio MCP テストで`match_scope`・`analogous_terms`のスキーマと不正 relation の拒否を確認。
- 独立レビュー（opus サブエージェント）の指摘を修正：(1) 複数クエリ変種で、別変種の依頼概念に一致する研究が類縁として返った → 全変種の概念一致の和集合で除外、(2) 絞り込みで 0 件になっただけなのに「依頼概念の研究はない」と案内した → `concept_filtered_out`、フォールバックに`status`・`analyzed_only`を適用、(3) LLM プロンプトでコード候補の relation 説明が類縁語の直後にあり混同を招いた → 位置を修正、(4) 類縁語の大小文字違いで relation 表示が「不明」になった → 大小無視で統合、(5) 類縁スコープ 0 件時の案内文、(6) CLI の引数不足。修正後の再現シナリオはすべて回帰テスト化した。全体 250 passed、ruff 合格、`uv build` 成功。
- 未検証：類縁語の臨床的妥当性は人手判断で、専門家レビューは未実施。LLM による類縁概念提案（`use_llm=true`）はモックなしの実 LLM で試していない。類縁概念の研究で`research_protocol`が類縁概念の定義を正しく答えるかは、実 PDF で未確認。

## 2026-09-27 クライアント翻訳を既定化（spec v0.6）

- 同梱辞書の廃止：`data/terminology.json` → `data/terminology.example.json`（ICD-10 名称 54 件を削除、既定では読まない）。wheel の同梱は `ema.sqlite3` と `ema-medicines.json` だけであることを `uv build` の生成物で確認した。
- 同梱カタログでの模擬実行：メインスレッドがクライアント役を務め、ICD-10 の指針に従って「1型糖尿病をアウトカムとした研究」を翻訳した（E10 Insulin-dependent diabetes mellitus の名称と一般名を英語名に、T1DM・juvenile diabetes を言い換えに、type 2 diabetes mellitus を sibling、diabetes mellitus を broader の類縁概念にした）。
  - `plan_study_search` は `needs_client_translation`・`client_expansion`・辞書なしを返した。
  - `role=outcome` の概念一致は 11 件、類縁スコープは 86 件で、各行に語の出所が付いた。
  - 未一致語ログは作成されなかった。
- テスト：辞書依存の 4 件は、記入例を利用者辞書として設定する形に置き換えた。辞書なしの既定、記入例の読み込み、ログ抑止、wheel に疾患辞書が入らないことのテストを追加した。テストは自動フィクスチャで利用者の `data/dictionaries/` から隔離した。全体 256 passed、ruff 合格。
- 独立レビュー（サブエージェント）の指摘を修正：`use_llm=true` の計画で `client_expansion.required` が真のまま残った／利用者辞書のコード候補の出所がファイル名なしの `dictionary` だった／隠しファイルの誤読とエラーにファイル名がない／存在しない `EMA_TERMINOLOGY_PATH` で `catalogue_status` も失敗した／空の辞書が未設定扱いになった／古い参照（`.env.example` など）。それぞれ回帰テストを追加した。
- 未検証：実クライアント（Claude Code／Codex）が ICD-10 の指針に従って出す類義語の質とばらつき（同じ質問での候補数の再現性）。同梱辞書で実測した関連語による再現率向上（v0.4：liver injury 50→103 件）が、クライアント翻訳で得られるかは測っていない。

## 2026-09-28 長い検索語の分解と近接距離の修正（spec v0.7）

- 発見の経緯：実際の検索（間質性肺疾患をアウトカムとする研究）で、5 語の検索語が単語に分解され、候補が 1,212 件になった。
- 修正後の同じ検索（同梱カタログ HEAD 版、14 変種、`role=outcome`）は 54 件で、手動回避の件数と一致した。PDF の取得は 0 件。
- テスト：近接距離（2 語→3、5 語→4、6 語→5）、隣接する 6 語の同義語が一致すること、`compare_protocols` の 5 語変種が分解されないこと、`search_studies` が分解を維持すること、の 3 件を追加した。いずれも修正前のコードで失敗し、修正後に合格することを確認した。
- 独立レビュー（サブエージェント）の指摘：距離を内容語の数だけで決めた中間版では、機能語を 2 つ以上挟む検索語（「risk of stroke in patients with atrial fibrillation」）が原文にも一致しなかった（SQLite で 0 件と確認）。4 語以下でも「Malignant neoplasm of bronchus and lung」が一致しない既存の穴があった。距離を元の文字列から決める方式に直し、両方を回帰テスト（2 件と距離指定の単体 1 件、いずれも修正前に失敗）にした。全体 262 passed、ruff 合格。修正後も ILD の例は 54 件で変わらない。
- 未検証：距離 `max(3, 語数 − 1)` は経験則であり、語順が大きく入れ替わった長い表記は取りこぼす可能性がある。

## 2026-09-28 検索評価用の正解集合（下付け段階）

- 目的：検索ロジックの変更で再現率が上がったか下がったかを測る（調査記録 `docs/research/202609281003_search_logic.md` の推奨の第 1 段階）。
- 作り方：
  - 20 問（アウトカム 10、対象集団 3、曝露 × アウトカム 7）について、6 つの検索戦略それぞれの上位 60 件を合わせてプールを作った（合計 1,569 件）。戦略は、現行手順の検索語（役割別の列）、同じ検索語で全列、コードのみ、広い種語、独立生成の検索語、カタログの condition facet。
  - opus サブエージェント 5 本が、カタログの登録内容だけで 3 段階（＋不明）の下付けをした。引用の実在は `merge` で機械的に照合した。エージェントが照合済みと報告した引用のうち 4 件は、実在しなかったため low に落とした。
  - 内訳：relevant 839、partial 305、not_relevant 372、unclear 53。ユーザーの確認待ちの優先行（unclear または low）は 116 行。
- 暫定の評価（ユーザー未確認の下付けによる、20 問の平均。relevant のみを正解とする相対再現率）：

| 戦略 | 相対再現率 | 平均取得件数 |
|---|---|---|
| 現行手順（役割別の列） | 0.871 | 65 |
| 同じ検索語で全列 | 0.959 | 91 |
| 独立生成の検索語 | 0.783 | 107 |
| 広い種語 | 0.548 | 102 |
| condition facet | 0.237 | 23 |
| コードのみ | 0.010 | 0.8 |

- 暫定の所見：
  - 役割別の列に絞ると、該当研究の約 9% を取りこぼす（Outcomes 欄が 19% 空であることと整合）。
  - カタログ本文にはコードがほとんど現れない。
  - 独立に生成した検索語は、現行の検索語と拾う研究が一部異なり、和集合を取る余地がある。
  - 再現率が最も低いのは q17（免疫チェックポイント阻害薬 × 肺臓炎、0.33）と q18（妊娠中の抗てんかん薬、0.50）。
- 限界：
  - 相対再現率なので、戦略どうしの比較にしか使えない。
  - プールは検索語ベースの戦略に偏っている。
  - ラベルはユーザー確認前の LLM 下付けである。
  - 評価は同梱カタログ（v0.2.1 の HEAD 版）で行った。

## 2026-09-28 階層つき語群と順位付けの評価（暫定ラベル）

- 仮説（ユーザー）：
  - メタデータには、固有名ではなくカテゴリー（上位概念、包括的なアウトカム名、薬効群）しか書かれていないことがある。
  - そこで、全列で広く拾ってから並べ替えるのがよい。
- 追加した戦略：
  - `tiered`：現行の検索語に、ブロックごとのカテゴリー語（ICD-10 のブロック・章名、MACE/AESI などの包括名、ATC の第 3〜4 階層名）を加え、全列で検索する。
  - これで新たに見つかった候補 239 件を、同じ手順で下付けした（relevant +37）。ラベルは計 1,808 件：relevant 876、partial 353、not_relevant 501、unclear 78。
- 相対再現率（relevant のみ、20 問の平均）：

| 戦略 | 相対再現率 | 平均取得件数 | 判定済みの中の適合率 |
|---|---|---|---|
| 現行手順 | 0.858 | 65 | 0.735 |
| 全列 | 0.944 | 91 | 0.623 |
| 階層つき（全列） | 0.967 | 144 | 0.508 |

- 大きく伸びた問：q17 0.33→1.00、q18 0.43→0.71、q14 0.79→1.00、q09 0.84→1.00。
- 伸びなかった問：q04・q05・q10 では、広すぎるカテゴリー語（"adverse drug reactions" など）が新しい該当研究を 1 件も加えなかった。カテゴリー語は概念ごとの具体性が要る。
- 順位付け（階層つきの候補集合、20 問の平均）：
  - RRF のみ：nDCG@5 0.883、上位 5 件の該当数 3.60
  - 固有語での一致を先に、次に役割の列で一致したブロック数：0.908、3.90
  - さらに研究タイプの適合（二次データ＋、調査−）：0.920、4.05
  - どの並べ方でも、上位 10 件に未判定の研究は 0 件（比較は公平）。
- 限界：
  - ラベルはユーザー確認前の LLM 下付けである。
  - 「役割の列で一致」という特徴量は、ラベルの判定基準（概念が求める役割で現れるか）と重なるため、効果がやや高く出る可能性がある。
  - LLM によるリランキングは、正解が LLM の下付けであるため評価していない。

## 2026-09-28 製品への実装（spec v0.8）

- `compare_protocols` に、概念ブロック、全列での取得、カテゴリー語、順位付け、`check_protocols` を実装した。
- 正解集合 20 問を、製品の `compare_protocols` そのもので評価した（一覧の上限を外して全順位を採点）。結果は相対再現率 0.965、nDCG@5 0.920、上位 5 件の該当数 4.10、平均候補数 144 で、評価スクリプトでの設計値（0.967／0.920／4.05／144）を再現した。
- テスト：`tests/test_ranking.py` 7 件を追加した（役割による順位付け、カテゴリー語の後置、ブロック間の AND、複数ブロックで 0 件の案内、研究タイプ、不正な組み合わせの拒否、プロトコルなしの後置と PDF 非取得）。実 stdio MCP テストで新しい引数のスキーマを確認した。当初のテスト案は、1〜5 件に一致して実サイトへ HTTP を送っていたため、一致しない語に直してオフラインにした。全体 276 passed、ruff 合格。
- 独立レビュー（サブエージェント）の指摘を修正し、回帰テストを追加した：類縁スコープで `check_protocols` を指定すると例外、従来形式で 21 件以上の検索語が検証エラーのまま出る、空白のカテゴリー語が全件に一致する、`network_requests` が研究数だった（HTTP クライアントに実要求数の計数を追加）、対象外になった研究の扱い、ブロックと `role` の併用が黙って無視される、ブロック間で出所 `category` が固有語を上書きする。全体 278 passed。修正後も製品評価の値は変わらない。
- 未検証：
  - `check_protocols` を実サイトで動かした確認（テストはモックのみ）。
  - 実クライアントが生成するカテゴリー語の質。
  - ユーザー確認後のラベルでの再評価。

## 2026-09-28 同梱カタログの更新（wheel インストール）

- `refresh_from_bundle` を追加した。同梱 DB の最新取込日時が手元の DB より新しいときだけ、手元の DB のカタログの表を同梱 DB の内容に置き換える。置き換えは 1 回の書き込みトランザクションで行い、解析キャッシュの表には触れない。
- 当初はファイルごと差し替える実装だった。独立レビューで次の指摘を受け、その場で置き換える方式に改めた。
  - 起動時に例外が漏れる
  - 失敗が続くと呼ばれるたびに 44 MB を複製する
  - 同時に起動した 2 つのプロセスが一時ファイルを奪い合う
  - 動作中のサーバーの書き込みが失われる
  - WAL の checkpoint の失敗を見落とす
  - `SELECT *` が列の位置に依存する
  - 同梱 DB を読むだけで site-packages に `-shm` を作る
- テスト：`tests/test_distribution.py` に 3 件を追加した。
  - 新しい同梱 DB への置き換え（新しい研究が検索でき、解析と回答が残る。2 回目は何もしない）
  - 利用者の取込のほうが新しい場合と、DB がない場合は置き換えない
  - 同梱 DB のスキーマ版が異なる場合、手元の DB は変わらない
- テスト全体は 281 passed、ruff 合格。
- 実データでの確認（使い捨てのスクリプトで、コピーに対して実施）：コミット済みのカタログ（3,312 件）を取込日時だけ新しくして同梱 DB に見立て、解析 3 件・回答 3 件を持つ手元の DB を更新した。
  - 所要時間は 0.57 秒だった。
  - 研究 3,312 件、解析 3 件、回答 3 件が残り、`integrity_check` は ok だった。
  - 解析済みの研究 3 件の索引の行は、更新の前後で一致した。
  - 同梱 DB の横に `-shm` は作られなかった。
  - 2 回目の判定は 1 ミリ秒だった。
- リリース後の確認（v0.3.1）：`uvx --from git+...@v0.3.1` を、使い捨てのデータフォルダ（`XDG_DATA_HOME`）で起動した。
  - データフォルダには、古いカタログ（研究 3,212 件、取込 2026-01-01、解析 3 件、回答 3 件）を置いた。
  - `catalogue-status` の実行時に、同梱カタログ（3,312 件、取込 2026-09-13）へ更新された。
  - 解析 3 件と回答 3 件は残り、`integrity_check` は ok だった。
  - このコンテナでは `PYTHONPATH=/workspace/src` が設定されており、そのままでは `uvx` がチェックアウトのコードを読む。確認では `env -u PYTHONPATH` で外した。
- 未検証：
  - 別のサーバーが書き込み中のときの待ち時間（最大 30 秒待つ）。

## 2026-10-01 カタログ更新（v0.3.2）

- 取込は種別タグを累積するため、旧スナップショット（2026-09-13）と混ぜずに、2026-10-01 の 4 ファイルだけを空の DB へ `import-all` した。
- 結果：研究 3,314 件、claims 819 件、ehr 916 件、registry 559 件、タグなし 1,511 件、解析 0 件、`integrity_check` は ok、スキーマ版 4。
- `catalogue-status` は `status: current`。テスト全体は 281 passed。

## 2026-10-01 リポジトリ名の変更

- GitHub のリポジトリ名を `rwd-catalogue-mcp` から `ema-rwe-mcp` に変更し、パッケージ名・MCP 名と揃えた。現在の案内（README、sample、`pyproject.toml`、`docs/release.md`）の URL を新しい名前にした。過去の記録は旧名のまま残す。
- GitHub は旧 URL を新 URL へ転送する。`rwd-catalogue-mcp` という名前で新しいリポジトリを作ると転送が止まるため、作らない。

## 2026-10-01 出典表示とデータの範囲

- `NOTICE` を追加し、同梱の EMA データ（カタログ DB、医薬品辞書、テスト用 HTML 断片）の出典・取得年月・© EMA・適用される EMA の Legal notice と、MIT がコードだけに及ぶことを記載した。`license-files` に加え、wheel の `dist-info/licenses/NOTICE` と sdist に入ることを `uv build` で確認した。
- カタログの登録内容を引用する `tests/fixtures/gold/pool/*.csv` を git 管理から外した（手元には残す）。テストは一時フォルダで動くため 281 passed のまま。過去のコミットには残る。
- 根拠：`docs/research/202610011523_ema_terms_public_release.md`。

## 2026-10-01 ATC 上位クラスのカテゴリー語（spec v0.9）

- 同梱カタログの集計：ATC 付きの exposures を持つ研究 1,541 件、ATC がクラスだけの研究 293 件、カタログに現れる第5レベル ATC 970 種のうち EMA 医薬品辞書にあるもの 463 種（研究とコードの組で 60.5%）。自由記述欄のコード体系の出現は各 0.2〜1.3%。
- 実カタログ（コピー）と実辞書で `compare_protocols(["apixaban"], role="exposure")` を実行し、`B01AF`／Direct factor Xa inhibitors が加わり、クラス名だけで記録された研究 5 件が後方の候補に入ることを確認した。metformin（辞書に単剤なし）では何も加わらない。nivolumab は L01FF／L01XC がコードだけで加わる（カタログに名称の記載がない）。
- テスト：`tests/test_ranking.py` に 3 件を追加した（クラスのみの研究を category として後置し同じクラスの別成分には一致しない、医薬品以外と重複では加えない、比較表の一致の根拠の表示）。`tests/test_analogous.py` の期待値に `match.sources` を加えた。全体 284 passed、ruff 合格。MCP の引数スキーマは変えていない（応答項目の追加のみ）。
- 未検証：正解集合での再現率・順位への影響。カタログ由来の名称の誤記。

## 2026-10-01 医薬品名の展開をカタログ由来に一般化（spec v1.0）

- カタログの医薬品記載の集計（3,314 研究）：exposures 欄が名前だけ 1,059、第5レベル ATC ＋名前 840、空欄 712、第5レベル ATC だけ 318、クラスの ATC を含む 385。カタログの第5レベル ATC 970 種のうち EMA 医薬品辞書と一致しないもの 507 種（成分が辞書にない 373、コードの版違い・欠けが 134）。
- クラスでの到達（研究数）：所属薬の名前での本文検索は、所属薬の ATC コードでの一致の約 2〜4 倍に届いた（N03A 74 対 35、B01AF 84 対 28、L04AF 98 対 25）。この結果から、名前で検索し、ATC は結合キーにだけ使う設計にした。
- 同じ研究内の併記からコードの無い名前に ATC を付ける案（511 語）は、無作為 40 語のうち約 5 語が比較対照薬・併用薬・合わせ剤への誤った対応だったため採用しなかった。
- 実カタログ（コピー）と実辞書で確認：apixaban → B01AF のクラス（後方）。metformin（EMA 辞書にない）→ A10BA Biguanides。afatinib → EMA の旧コード L01XE13 とカタログの L01EB03 の両方のクラス。antiepileptics → 所属 49 成分の名前、候補 88 件。pancreatitis → 展開なし。
- テスト：`tests/test_ranking.py`（成分のクラスの後置、クラス名からコード無しの所属薬への到達と出所、EMA 辞書にない薬）、`tests/test_drugs.py`（合わせ剤を単剤から解決しない、第2レベルの入力コードを ICD-10 と衝突するため解決しない、EMA とカタログのコードの併用）。全体 290 passed、ruff 合格。MCP の引数スキーマは変えていない。応答の `atc_class_terms`（v0.9、未リリース）は `medicine_expansion` に置き換えた。
- 独立レビュー（サブエージェント）の指摘を修正した：単独では意味のない名称（`combinations` など）を検索語にしていた、100 語の上限が質問単位でなかった、上限でクラス自身の名称が落ちえた、別ブロックで呼出元が書いた語の出所を付け替えていた、ATC の形をしただけの非 ATC コード（Read の `C10E` など）を解決していた、小文字のコードを解決しなかった。
- 未検証：正解集合での影響。クラス名の表記揺れ（カタログの名称と完全一致しないクラス名は、呼出元が ATC コードを渡す必要がある）。

## 2026-10-01 プロトコル取得で CSV 由来のカタログ項目が消える不具合の修正

- 症状：`get_protocol`（内部で `get_study`）を呼んだ研究は、exposures・conditions・outcomes・objective が空になり、一部は catalogue_data_sources も空になった。そのため医薬品・疾患・アウトカムの検索に一致しなくなった。医薬品入力の検索テストで PDF を取得した 60 研究すべてで確認した（exposures 60/60、objective 60/60、outcomes 52/52、conditions 49/49、catalogue_data_sources 11/30 が空）。
- 原因：`get_study` は研究ページとタブから `parse_study` で新しい Study を作るが、`parse_study` はこれらの項目を読み取らない。保存は `INSERT OR REPLACE` でレコード全体を置き換えるため、CSV にしかない値が失われ、全文索引も空の値で作り直された。
- 修正：ページから値が得られなかった項目は、保存済みの値を残す（`CATALOGUE_FIELDS`）。
- テスト：`tests/test_screening.py::test_protocol_retrieval_keeps_catalogue_only_fields`（修正前は失敗することを確認した）。

## 2026-10-01 合わせ剤の入力が単剤に解決される不具合の修正（spec v1.0）

- 症状：医薬品入力の検索テストで、"empagliflozin and metformin"・"nirmatrelvir and ritonavir" が単剤の ATC にも解決され、単剤名（empagliflozin）が検索語に加わった。
- 原因：EMA 医薬品辞書の照合（`drug_expansion`）が入力文字列の中の各成分名に個別に一致し、`expand_medicine` がその結果をそのまま使っていた。
- 修正：成分をつないだ入力は成分の集合として扱い、集合全体が一致する合わせ剤にだけ解決する。`drug_expansion` も、入力全体がある合わせ剤の成分構成と一致するときはその合わせ剤だけを返す。
- 実データ：empagliflozin and metformin → A10BD20 のみ。nirmatrelvir and ritonavir → J05AE30 のみ。Synjardy → A10BD20。metformin・apixaban・antiepileptics は変化なし。
- テスト：`tests/test_drugs.py::test_combination_query_resolves_only_to_the_whole_ingredient_set`。

## 2026-10-02 医薬品入力の検索テストと修正（spec v1.1）

- テスト：医薬品の入力102ケース（日本語51・英語51。成分・製品・クラス・合わせ剤、DB にある薬と無い薬、ATC の版ずれを含む）を、期待値を見ないクライアント役のサブエージェントが MCP で検索し、取得した PDF 79件を別の担当が照合した。詳細は手元専用の調査記録 `docs/research/private/202610020900_drug_search_test.md`（入れ子の非公開リポジトリ。カタログ・PDF の引用を含むため公開しない）。
- テスト中に見つけた不具合：PDF の取得でカタログの医薬品・疾患・アウトカム・目的が消える（2026-10-01 の項）、合わせ剤が単剤に解決される（同）、EMA 辞書の ATC 誤記による別の薬への展開（roflumilast）、長い語の一部への一致（glucagon-like の glucagon）、プロトコルの無い研究・画像だけの PDF が1位に来る。
- `protocol_listed` の妥当性：テストで get_protocol を呼んだ研究のうち、プロトコルが無かった研究はすべて CSV に所在が無く（37/37）、所在が無いのにプロトコルがあった研究は3件だった。
- 同梱カタログ DB：同じ 2026-10-01 の4つの export から作り直し、`protocol_listed` を記録した（3,314件。所在あり 2,037、なし 1,277）。既存の値との違いは取込時刻 `data_source_types_checked_at` だけである。
- 再テスト（修正後の MCP）：誤った薬への展開の消失、プロトコルの無い1位の解消（19ケースすべてで新しい1位のプロトコルを取得）、画像だけの PDF の後置（5ケース）、DB に無い薬での類縁概念（ATC コードのある8件で提示）、語形・略語の変種での到達（前回候補外の13件中12件）を確認した。102件の回帰では、期待値の研究が候補から外れたのは1件で、誤った語でしか届いていなかったものだった。
- 再テスト後の追加修正：EMA 辞書の ATC コードをカタログの検索語・`code_searches` から外した（PDF 本文検索のヒントには残す）。`query_expansions` も語全体で照合する。塩の語を除いて照合する。類縁概念が無いときの医薬品向けの指示を加えた。
- 独立レビュー（サブエージェント）の指摘を修正した：`plan_study_search` の `queries` に EMA 辞書の ATC コードが残っていた、呼出元が渡した ATC コードが EMA 辞書の出所で上書きされてカタログ検索から消えていた、類縁概念を `match_scope=analogous` で再実行すると ATC 由来の類縁概念が消えていた、旧版と同じデータフォルダを共有したときの DB 互換（スキーマ版を 5 に上げ、旧版には明示的なエラーを出させる）、キャッシュ済み PDF の再解析、クラスのカテゴリー語の条件、README と仕様の「語全体の照合」の書き方、空振りしていたテスト。
- 未対応（記録のみ）：辞書側の塩付きの成分名（dabigatran etexilate など）は INN だけでは一致しない。CSV の再取り込みや同梱 DB の入れ替えで `protocol_text_layer` が消え、次の PDF 取得で再判定される。
- テスト：`tests/test_ranking.py`（プロトコル所在・テキスト層による順位、0件の医薬品の類縁概念、類縁概念での再実行）、`tests/test_screening.py`（テキスト層の記録と保持、画像だけの PDF、所在の取り込み）、`tests/test_drugs.py`（ATC コードを情報源をまたぐ鍵にしない、語全体の照合、塩の語）。全体 300 passed、ruff 合格。
- 残る課題：サーバーが作った類縁概念の `matched_term_sources` が `caller` と表示される。類縁概念の候補一覧にはプロトコル所在の並べ替えが効かない。カタログに名称の無いクラスでは上位概念を示せない。`protocol_listed=false` の見逃し（プロトコルがあるのに所在が無い研究）は下がるだけで、外れはしない。1位の PDF が入力の薬を曝露でなく比較群などとして扱う例がある（仕様どおり下げない）。

## 2026-10-02 取得で分かった事実の別表保存と医薬品欄の補完（spec v1.2）

- 実装：`protocol_observations` 表（DB スキーマ 6）、`ema-rwe backfill-protocols`、`ema-rwe merge-observations`、類縁概念の順位付けと出所表示、第3レベルでの上位概念、DB で観測した塩の語と基名の鍵。
- 独立レビュー（サブエージェント）の指摘14件を修正した：中断した研究が再開で飛ばされる、一時的な通信障害で残りが恒久的に除外される、PASS 表の欄名を本文の語句でも拾い除外基準の薬まで補う、類縁概念の語の句読点で出所と件数がずれる、空白の類縁語でカタログ全件が候補になる、旧版の取得でテキスト層を上書きする、確認の結果が順位に反映されない、移行の版を先に設定する、同梱観測の統合で1件の失敗が全体を捨てる、文章からの補完で複数語の製品名が誤一致する、ATC コードを欄の全成分に付ける、配布に PDF ID やエラーが入る、など。
- 補完の実行（2026-10-02、要求間隔15秒、研究の間60秒）：医薬品欄が空の712研究のうち、CSV にプロトコルの所在がある347研究をすべて処理した。429 と 5xx は0回、プロトコルの無い研究は0件（所在があればすべてあった）。
  - PASS 情報表から医薬品を補えた：63（最終点検の修正後に PDF を読み直して 65。うち ATC コード付きの医薬品 29）
  - PASS 表はあるが、欄に既知の医薬品名が無い（クラス名だけなど）：89
  - PASS 表が見つからない（DARWIN EU 様式、古い様式、2段組みなど）：192
  - 画像だけの PDF（OCR が必要）：3
  - 文章（題名・説明・目的）から補えた：195研究（通信なし。修正後は1語の製品名を戻して197）。どちらかで医薬品が1つ以上補えた研究は、最終的に209/712。
- PASS 表の取り出しの確認：本文の語句（"medicinal products ... valproate ... metformin is an exclusion criterion"）からは何も補わない。2段組みのページ（研究 37810）は拾えない（誤って補うより補わない方を選んだ）。
- 公開前の最終点検（サブエージェント）の指摘7件を修正した：ATC コードが次の医薬品に付く（「名前 (コード), 名前 (コード)」でコードを直前の名前に付けるよう直し、同梱 DB の観測を `--reextract` で作り直した。例：48948 の enzalutamide に abiraterone のコード L02BX03 が付いていた）、恒久的な 4xx で補完が毎回同じ研究で止まる（`EMA_HTTP_ERROR` として終わったものにする）、括弧付きの1語の製品名（Spikevax など）が文章からの補完に使われない、同梱の観測が研究単位でしか届かない（項目単位で補う）、`merge-observations` が失敗を隠す、カタログが医薬品欄を埋めた研究に文章由来の値が残る、CHANGELOG の記述漏れ。
- 同梱 DB：作業用 DB の観測432件（医薬品あり209件）を `merge-observations` で加え、スキーマ 6 に上げた。研究の記録は変わらない。配った観測に PDF ID とエラーは無い。補完した医薬品（例：duloxetine 32283、rivastigmine 27280、etanercept 25620、abrocitinib 50474）で、exposure の役割の検索に一致することを確認した。
- テスト：観測の表（記録・順位・移行・同梱の統合・配る項目）、補完（文章、PDF、429・通信障害での停止と再開）、PASS 表の取り出し、類縁概念（順位・出所・件数・空白の語・第3レベル）、塩の基名。全体 315 passed、ruff 合格。
- 残る課題：文章から補った医薬品は、比較対照や除外基準の薬であることがある（`observed_exposures` の出所で区別し、プロトコルで確かめる）。2段組みの PASS 表、クラス名だけの PASS 表、OCR が必要な PDF からは補えない。

## 2026-10-03 PASS 表のクラス名と ATC コード、文章からの補完の誤り防止（spec v1.3）

- 背景：補えなかった研究を、保存済みの 347 件の PDF を通信なしで読み直して分類した。「PASS 表はあるが既知の名前が無い」研究は、修正後の読み直しで 87 件あった。その大半（約 65 件）は欄が NA／Not applicable／空欄で、補う医薬品が無い。具体的な薬が取れていないものが約 8 件、クラスの列挙が約 8 件だった。「PASS 表が無い」192 件は、約 6 割が医薬品を主題としない研究だった。本文に ATC コードがあるのは 19 件で、曝露のほかに除外基準（26340）、アウトカムの定義（30770）、共変量、データ記録の例（28451）にも使われていた。このため本文のコードは記録しない。
- 実装：欄のクラス名と ATC コード、行で分かれたコードの連結、「コード (名前)」の書式、名前の後に作用の語が続く場合の除外、研究の略称と測定される物質の除外、™／℠ の正規化、記録したコードの索引、出所ごとの抽出規則の版（`exposure_rules`）と、同梱 DB の新しい版による置き換え。
- 作業用 DB の読み直し（`backfill-protocols --no-download --reextract`、344 件）の変更前後の差分を、すべて PDF の欄の原文と照らした。
  - 医薬品を補えた研究：209 → 215。PASS 表由来：65 → 76。
  - 除いた誤り：zaleplon（1000000428、略称 SONATA study）、nitric oxide（26465、27888、106968。呼気 NO）、angiotensin II（45122、1000001013。ARB の語の一部）。
  - 直したコード：105061（L02BX03 を enzalutamide から abiraterone へ）、29795（`J06B A02` → J06BA02）、34474（`N03A B05` → N03AB05）、42377（misoprostol のコード A02BB01 を diclofenac に付けない）。
  - 新たに補えた例：VIZAMYL™（26709、26712）、loperamide（32664、コード A07DA03 として）、teicoplanin（35696、J01XA）、DIURETICS／Beta blocking agents（1000001065）、ACE 阻害薬・ARB の各クラス（45122）、HMG CoA reductase inhibitors（1000001100）、VACCINES（1000000193、21757、41807）、ANTIPSYCHOTICS（1000000333、1000000827）。
- 同梱 DB：`merge-observations` で作業用 DB の観測を統合した（428 研究が変更）。同梱 DB と作業用 DB の医薬品は一致した。配る観測に PDF ID とエラーは無い。v0.5.0 の同梱 DB を統合済みの利用者 DB を想定した複製に統合すると、医薬品の値が同梱 DB と一致し、医薬品の列の検索で ACE inhibitors → 45122、A07DA03 → 32664、N03AB05 → 34474、flutemetamol → 26709・26712 に届いた。
- 独立レビュー（サブエージェント）の指摘7件を修正した：第2レベルのコードの名称の条件が常に真になり、カタログに名称の無い ICD-10 の分類（M05、J06）を ATC として記録する、辞書に無い薬のコードが隣のコードを越えて別の薬に付く、「INN コード (ブランド)」の欄全体を「コード (名前)」の書式とみなす、カンマ区切りのコードをつなぐ（`B01A, C10` → B01AC10）、変異名（G12C）をコードとして拾う、汎用の名称（combinations）を名前にする、クラス名の中の医薬品名（heparin）を別に記録する。あわせて、作用の語を名詞と -ing 形に限り（inhibits などの動詞では外さない）、略称の study などの語は先頭だけ大文字でも数えるようにした。修正後の読み直しの差分は、修正前に原文と照らした差分と同じだった。
- テスト：PASS 表のクラス名とコード、商標記号、辞書に無い薬のコード、NA の欄、文脈の無い第2レベルの形、「コード (名前)」の書式、行で分かれたコード、receptor の語、略称と測定物質、新しい規則の版による置き換え（同じ版では手元の値を優先）、レビューの各再現例。全体 320 passed、ruff 合格。
- 残る課題：検索時に zaleplon を製品名 Sonata へ展開すると、題名の「SONATA study」に一致する（題名は役割を問わず検索される）。これは補完ではなく検索語の展開の問題として残す。欄が 300 字で切れるとクラスのコードが落ちることがある（1000001065 の calcium channel blockers）。名前もコードも書かれていない欄（AZD1222、Gi(l)otrif® など）からは補えない。

## 2026-10-03 ドキュメントと実装の照合、手順書の MCP リソース（spec v1.4）

- 全ドキュメントの点検（サブエージェント2体。README／README_DEV と、その他の文書・設定例・ツールの説明・CLI のヘルプ）で見つかった食い違いを直した。主なもの：`.env.example` の空の整数値（`LLM_BATCH_CHARS=` など）で起動時に `ValueError` になる、`LLM_MAX_TOKENS` の既定値、`facets.conditions` と `compare_protocols` の `role` を絞り込みと書いていた、`pending_tools` の位置、類縁概念の再実行で `blocks` を併用できない、プロトコルの版の分類（Updated とそれ以外の2種類）、`EMA_CACHE_TTL_SECONDS` の用途、サーバー側抽出の条件（`compatible` でも `LLM_BASE_URL` があれば有効）、内部探索の全文ルートとタイムアウト、存在しない関数名（`preference_for`）、Codex 設定例の疾患辞書の同梱と、`<checkout>` を含んだまま有効な Playwright のブロック。
- docs/clinical-search.md（医薬品の照合・展開・補完）と docs/comparisons.md（概念ブロック・順位付け・候補一覧・類縁概念）を、v1.0〜v1.3 の実装に合わせて書き直した。
- 手順書を wheel に同梱し、MCP リソースとして返すようにした。`uv build` の wheel に `ema_rwe/docs/mcp-workflow.md` が入ること、実 stdio の MCP テストでリソースの一覧と読み出し、`instructions` の案内を確かめた。
- テスト：`test_mcp.py`（リソース）、`test_distribution.py`（wheel への同梱とパスの切り替え）。全体 321 passed、ruff と `uv build` 合格。
- コードの判断が要るもの（未対応）：CLI と MCP の引数の対応の欠け（`analyze --detail`、`cache-analysis --batch-offset` など）、CLI の `search` の既定が DARWIN EU のみ（ヘルプに明記した）、`get_study` の再取得が種別タグを詳細ページの F8.7 で置き換えること（AGENTS.md に明記した）、`.devcontainer` の旧名とインタープリタのパス。

## 2026-10-03 CLI と MCP の一致、書き出しの種別タグの保持

- `get_study` の再取得で、CSV から取り込んだ研究の種別タグ（`data_source_types`・出所・確認日時）を保つようにした。書き出しに無く詳細ページだけで知った研究は、これまでどおり F8.7 の値を使う。テスト：`test_page_refresh_keeps_the_typed_export_source_tags`（修正前は失敗する形）。
- CLI の `search` の既定を MCP と同じ全研究にした（`--darwin-only` で DARWIN EU に限る。`--all-studies` は受け付けて無視）。実測：`search diabetes` は 292 件、`--darwin-only` は 8 件。MCP にあって CLI に無かった引数を加えた（テスト：`test_cli_takes_the_mcp_arguments_and_searches_every_study_by_default`）。
- `.devcontainer/`（Git 管理外）の名前とインタープリタのパスを直した（手元の環境だけ）。
- リリース前の点検（release-consistency-audit スキル：機械的な走査と、読み取り専用のサブエージェントによる照合）で、次の2件の不具合を見つけて直した。1つは、CLI の `analyze --detail` の選択肢を MCP と違う compact/full にしていて、既定のままでは必ず `INVALID_INPUT` になっていたこと（summary/full に修正）。もう1つは、種別タグの保持が1回目の再取得にしか効かず、再取得で `metadata_source` が詳細ページの値に変わるため2回目からは F8.7 に置き換わっていたこと（`metadata_source` も引き継ぐように修正。テストは2回の再取得で確認）。あわせて、サービス層の `search_studies` の既定も `darwin_only=False` にし、docs/source-types.md の種別タグの説明を直した。機械的な走査では、README_DEV.md に残っていた古い版の例（`v0.5.1`）も見つけ、版に依存しない書き方にした。
- 全体 323 passed、ruff と `uv build` 合格。

## 2026-10-05 同梱 DB の作成手順に VACUUM を追加

- `import-all` と `merge-observations`（同梱 DB を作る2つのコマンド）の最後に `VACUUM` を実行し、前後のファイルの大きさを返すようにした（`Repository.vacuum`）。起動時の同梱観測の統合（`merge_bundle_observations(strict=False)`）では実行しない。
- 同梱 DB の複製で実測した：45,785,088 → 43,094,016 バイト（-2.7 MB）、gzip 圧縮後 17.1 → 12.1 MB（-29%）。研究 3,314 件と検索結果（apixaban 60 件）は変わらない。コミット済みの同梱 DB は、次の作り直しのときに詰め直す（DB を追加でコミットすると、その分 Git の履歴が増えるため、今回は書き換えない）。
- テスト：`test_vacuum_compacts_the_bundle_without_losing_rows`。全体 324 passed、ruff 合格。

## 2026-10-05 外部レビュー指摘 6 件の修正と実プロトコルでの前後比較

- 修正（1 指摘 1 コミット）: しおりの章境界で前ページの見出し・親章を引き継がない／空白だけの引用を拒否／サーバー側抽出のタスクを (研究, fingerprint) で管理し、PDF 更新時は旧タスクを取り消す／バッチ統合と `missing_information` の上限超過を件数付きで記録（`with_notes`、質問別回答も同様）／カタログの ATC 名と EMA 成分の一致に両方向の成分対応を要求／同梱データの初回コピーを `atomic_write` で行う。各修正に、修正前に失敗する回帰テストを加えた。
- 前後比較の方法: 修正ごとに同じ検査スクリプト（スクラッチ、Git 管理外）を直前のコミットと修正後で実行し、出力を比べた。対象は実プロトコル 10 件（49733・19786・1000000308・20765・49003 は過去に抽出の問題を記録した研究。しおりあり 6 件・なし 4 件）のセクション分割と読解順、既存の実抽出 7 件の引用 571 件の検証結果、実抽出をバッチに分けた統合結果、カタログの医薬品名 5,503 語の展開結果。差分の採否は、メインセッションとは別のサブエージェント 2 体が PDF 実物・辞書・DB を読んで独立に判定した（報告は `docs/agent_report/202610050953_opus-eval_*.md`、Git 管理外）。
- 修正 1 の途中版では、章の途中で始まるしおりでページ先頭の前章の続き（1000000308 p.34 の解析上の限界）が見出しを失い、トップレベルの「12 Annexes」が親章を上書きして 49003 の Annex 3（筋毒性・肝毒性のアウトカム用語、p.62）が読解順から外れた。見出しの無いページだけで引き継ぎを切る、役割の分かるしおり章だけで親章を更新する、付録の参照判定にしおりの階層全体を使う、の 3 点に改めた。修正 5 の途中版（塩の語を除いた成分集合の完全一致）は、実カタログで linzagolix choline・botulinum toxin・human coagulation factor IX・ワクチン名などの一致を失ったため、両方向の対応へ改めた。
- 判定: 修正 1 は 1000000308（Abstract と目的 7.1/7.2 が読解順に入った）・19786（Abstract p.7 が入り、参考文献 p.52–55 が外れた）・49003（改訂表の続き・マイルストーン・背景 p.15–17 が外れ、Annex 3 は維持）で改善、ほか 7 件は同等。軽微な副作用として、1000000308 の改訂表の 3 行が読解順に入り、19786 p.4 の前文にあるデザイン記述は方法系の根拠として拒否される（同じ内容は Abstract と本文にある）。修正 2・3・5・6 は実データの出力が同一（修正 2 は保存済みの全引用 1,898 件で拒否 0 件、修正 5 はコードを共有する実在の組 553 件で判定の変化 0 件）。修正 4 は実抽出 7 件を 1 回で統合したとき、残る事実は同一で、切り捨て（key_notes 42→20、figure_pages 15→10、time_windows 26→20）と欠落情報の省略件数が正確に記録される。監査メモの重複付加も無くなった。
- テスト: 全体 333 passed、ruff 合格。
- 限界: 読解順が変わった 3 研究には既存の実抽出が無く、抽出結果そのものの前後比較（LLM 抽出の再実行）はしていない。判定は抽出に渡す本文の正誤による。部分文字列による医薬品名の一致は従来どおりで、aprepitant と fosaprepitant を同じ薬とみなす。

## 2026-10-05 プロンプトから個別事例に由来する記述を除く

- 調査：抽出・探索・検索計画のプロンプトと抽出後の監査を読み、git 履歴で出所をたどった。研究 ID・薬剤名・データベース名で分岐するコードは無かった。ロモソズマブ（1000000308、MID-NET）の検証後に足した 3 か所（「two types of outcomes」の件数指示と `COUNT_CLAIM` 監査、abstract/feasibility の節名を挙げたデータベース収集、同じ病院ネットワークの例を挙げた連結の基準）が個別事例に寄っていた。`COUNT_CLAIM` は手元の 433 件中 2 件（うち 1 件は 1000000308）にしか一致しなかった。`CCI` の黒塗り検査はアウトカム記述の近くで 36 件（8%）に一致し、EMA の一般的な慣行として残した。
- 修正：件数の指示を任意のリスト（アウトカム・エンドポイント・コホート・解析・定義）に一般化し、`COUNT_CLAIM` 監査を外した。データベースは節名を挙げず「この研究について名前の出るすべてのデータベースを、本文が支える usage で（評価しただけのものは candidate）」とした。連結の基準から MID-NET 由来の例を外した。翻訳の指示を「日本語」から「英語以外」へ、チェックリストの例を一般化し、検索計画のカテゴリー語と略語の例を例示と明記した（`docs/mcp-workflow.md` も同じ）。探索の版を v7 に上げ、プロンプトを含む fingerprint とあわせて既存のキャッシュは再抽出の対象になる。
- 検証：ユーザーの指示により、実プロトコルでの抽出の前後比較は行っていない。既存テストの全体実行のみ（`COUNT_CLAIM` を検査していたテストは黒塗り検査だけを残す形に改めた）。
- 未対応（別ブランチで検討）：見出し・役割の語の一覧、チェックリスト判定、`SALT_WORDS` など、EU PASS テンプレートや辞書の時点に依存する規則。

## 2026-10-05 構造推定規則の汎用化案のレビュー

- main `3da4933` の提案文書と `pdf.py`・`drugs.py`・`medicines.py`・解析fingerprintを照合した。実装は変更していない。詳細は `docs/research/20261005_structural_heuristics_review.md`。
- ローカルPDF8件を現行コードで節分割し、50075の有害事象定義・同意条件、1000000479の改訂内容・付録一覧、106363の目次とチェックリストの題名をテキスト層で確認した。合成入力で administrative の引用が `EVIDENCE_WRONG_SECTION` になることも確認した。
- `uv run pytest -q tests/test_bookmarks_cohort.py tests/test_extraction_audit.py tests/test_drugs.py`：44 passed。
- 限界：提案の433件集計は元のスクリプト・PDF集合がなく再現していない。提案方式の実装、全件の前後比較、LLM再抽出は未実施。EMA様式PDFはブラウズ時429で取得できず、hash・詳細な章構成・利用条件は独立検証していない。

## 2026-10-05 構造推定の汎用化（spec v1.5、parser structural-v24）

- 実装：役割による三つの判断（読む対象、引用の受理、抽出の指示）の分離、ローマ数字と様式の章名を含む共通の番号解析、検証済みの目次による節の始まりの検出、文字の大きさと章番号の並びによる章見出しの補完、質問票の構造に基づくチェックリストの範囲。設計の根拠は `docs/research/202610051802_structural_heuristics_generalization.md`（レビューを受けて確定した方針の節）。
- 計測：`scripts/structure_eval.py`。手元の 435 件（OCR が必要な 4 件を除く）を、テンプレートの系列で開発側 260 件と評価側 175 件に分けた（`tests/fixtures/structure/split.json`）。評価側 12 件のラベル（見出し 313、抽出に必要な記述 138、チェックリスト 5）は、実装を見ないサブエージェントが本文だけから付けた（`tests/fixtures/structure/labels.json`。人手の確認は未実施）。
- 変更前（8b8c0ad）→ 変更後（b4fae8c）の結果：

| 指標 | 変更前 | 変更後 |
|---|---|---|
| しおりを弱い正解とした最上位の章見出しの検出（本文だけから） | 731 / 1,293 | 1,099 / 1,293 |
| しおりを弱い正解とした見出しの検出 | 4,544 / 5,869 | 5,245 / 5,869 |
| しおりに無い検出行 | 1,969 | 1,390 |
| しおりの無い PDF の様式の章見出し | 940 / 1,222 | 1,220 / 1,222 |
| ラベルの見出しの検出 | 224 / 313 | 291 / 313 |
| ラベルの抽出に必要な記述が読む対象に入る | 126 / 138 | 137 / 138 |
| ラベルのチェックリストの本文が読む対象に入る文字数 | 30,172 | 5,069 |
| 参考文献らしい節を読む文字数（近似） | 313,718 | 220,219 |
| 既存の実抽出 571 引用の検証（受理／拒否） | 551 / 20 | 551 / 20 |
| 読む文字数 | 27.6 M | 31.4 M（+14%） |

- 独立評価：別のサブエージェントが PDF の本文を読んで、読む対象が変わった節の正誤を判定した。1 回目（41 件の判定と全件のスクリーニング）で悪化 19 件（目次の章のずれ、役割の無い下位節の除外、コード表の行の見出し化など）を見つけ、汎用の規則で直した。2 回目（62 件）では 19 件すべてが直り、新たな悪化 3 件のうち 2 件（107620 の目的、28295 のデータソース）を直した。最後の差分の確認（28 件）では悪化 0 件だった。
- 抽出そのものの比較：読む対象が大きく変わった 2 件（48872、46296）で、変更前後の本文を別々のサブエージェントが同じ指示で抽出し、どちらが新方式かを知らない判定者が PDF と照合した。2 件とも新方式が優位（確信度は中と低）。新方式の 46296 は当時付録を読んでいなかったが、その後の修正で付録（コード表）も読むようになった。
- 全 PDF の行単位の比較：変更前に読まれ変更後に読まれない行のうち、変更後の役割が背景・参考文献・管理・チェックリスト・目次以外のものは、1 件の PDF で最大 16 行（題名ページ、著者一覧、略語一覧など）。
- テスト：`tests/test_structure.py` ほか。追加した回帰テストは修正前のコードで失敗することを確かめた（一部は既存の振る舞いを守るためのテスト）。全体 382 passed、ruff 合格。
- 限界：評価側ラベルで見つけた問題（19786 の組入条件、12698 のスペイン語の本文）を直したため、評価側はこの 2 件について未調整ではなくなった。50436 の背景の章にある AESI の表（方法の節が参照する）は読まれないまま。英語以外の文書は unknown の節をすべて読むので、読む量が増える（30695 で +516k 字）。ラベルは人が確かめていない。抽出そのものの比較は 2 件だけである。作業中に、未 push のコミット 1 件を `git commit --amend` で修正した（版番号の更新を含めるため）。

## 2026-10-06 塩の語の一覧と辞書更新時の候補報告

- `SALT_WORDS` に choline、diolamine、meglumine、semisodium、anhydrous を加えた（2026-10-05 のカタログと EMA 辞書で「X W」と「X」が両方ある語のうち、対イオンか水和の語）。別の物質・製品系列・剤形を表す語（disoproxil、pegol、deruxtecan など 14 語）は `NOT_SALT_WORDS` として記録した。
- `refresh_drug_dictionary` は、どちらにも無い候補を `salt_word_candidates` で返す。現在の辞書では空。
- 実データでの変化：カタログの医薬品名 5,503 語の展開のうち変わったのは 4 語で、すべて同じ有効成分の別の塩の製品が加わった（TAFAMIDIS MEGLUMINE → Vyndaqel ほか、linzagolix → Yselty、treprostinil と TREPROSTINIL SODIUM → Orepaxam）。ほかの医薬品の展開は変わらない。
- テスト：`test_refresh_reports_unreviewed_salt_word_candidates`（修正前に失敗）。全体 383 passed、ruff 合格。

## 2026-10-06 英語以外のプロトコルの見出しの英訳（spec v1.6）

- 対象：手元の 435 件のうち本文が英語でない 21 件。見出しを検出できた 15 件（スペイン語、カタルーニャ語、ポーランド語。しおりのある 3 件と評価側ラベルの 12698 を含む）で、翻訳なし（main と同じ）と翻訳ありの読む節を比べた。残る 6 件は見出しが 0 件で、翻訳しても変わらない。
- 英訳は、呼び出し側の経路と同じ指示文を渡した別のサブエージェント（sonnet）が作った（565 見出し）。
- 最初の規則（翻訳のある文書は unknown の節を英語の文書と同じ規則で読む）では、独立の判定者（opus）が 15 件中 2 件を悪化と判定した。1000000544 は役割の語の無い見出しの節にある目的を失い、40729 は参考文献のしおりの章に入った附属書の標本サイズ計算を失った。どちらも英訳の誤りではなく、英語の手がかり語が英語でない本文を読めないことが原因だった。
- 英語でない文書では翻訳があっても unknown の節を読むように直した。直した後に読まなくなる節は、判定者が判定した節の部分集合で、すべて不要（背景、参考文献、表紙など）と判定されたものである。2 件の悪化箇所は再び読まれる。12698 のラベルの記述 12 件は、どちらの規則でもすべて読まれる。
- 読む文字数（15 件の合計）：1,631,068 字 → 1,542,177 字（−5.5%）。減ったのは 10 件（12698：64,709 → 44,138 字、40729：61,435 → 41,408 字など）。
- 30695（52 万字増の事例）は変わらない（600,186 字）。研究計画書ではなく現場の手順書で、英訳しても見出し 326 件中 304 件が役割の語に当たらず、v1.5 の「見出しの6割超が unknown なら unknown の節を読む」規則が働く。この規則を外しても、目的の節（20 ページ）以降は本文が始まった後の節として読まれる。
- テスト：`tests/test_heading_translation.py` 8 件（翻訳による役割、unknown の節を読み続けること、翻訳の検査、呼び出し側の経路、翻訳しない経路、英語の文書の拒否、内部 LLM の経路、比較表の fingerprint が翻訳で変わらないこと）。役割の判定で翻訳を使わない変更と、unknown の規則を戻す変更で、それぞれ該当するテストが失敗することを確かめた。全体 391 passed、ruff 合格。
- 未検証：内部 LLM による実際の英訳（テストは応答を模擬）と、実際の MCP クライアントでの往復。

## 2026-10-06 v0.5.4 のリリース前の整合性監査

- 読み取り専用のサブエージェント 3 体が、README 2 件、docs と設定の見本、プログラムが出力する文言（MCP の説明、応答の指示、プロンプト、CLI）をコードと照合した。
- コードの修正：CLI に `cache-headings` と `cache-answer` を追加した（どちらも以前は CLI から保存できず、`analyze` と `ask` の指示に応えられなかった）。英訳を質問別回答のキャッシュの鍵に入れた（英訳の前の回答が英訳の後に古い役割のまま再利用されていた）。`analyze_protocol` の説明に `needs_heading_translation` を、比較の指示に `extracting` の扱いと、`needs_heading_translation` を `research_protocol` より先に解決することを加えた。`translations_cached` に `study_id` を加えた。サーバー側の英訳の失敗をそれと分かる文言にした。`salt_word_candidates` に保守者向けの注記を付けた。`reading_order` の説明をページ順に直した。
- 文書の修正：ツール数（17 → 18）、引用を拒否する役割（管理章ではなく目次）、`pdf.py` と `llm.py` と `drugs.py` の役割、入力範囲、19786 の読取量（再計測で 44.8 万字 → 11.1 万字）、`role_basis` の `content`、目次から章を付けないこと、DB スキーマ 6、抽出スキーマ 0.3、`category` の出所、`.env.example` の相対パスの注意、リリース手順の確認項目など。
- 残したもの：同梱 DB（`data/ema.sqlite3`）には空きページが 352 ある。次の再構築（`import-all`）で詰まる（AGENTS.md の記述を「再構築したファイル」に直した）。
- テスト：`test_cli_saves_heading_translations`、`test_cli_saves_a_question_specific_answer`、`test_failed_server_translation_keeps_the_provider_message`、英訳の前後で回答の鍵が変わることと英語のプロトコルの鍵が変わらないことの確認を追加。全体 394 passed、ruff 合格、`uv build` で wheel に DB、医薬品辞書、手順書が入ることを確認。
- 修正の差分は、修正に関わっていない別のサブエージェントが照合した（1 回目の指摘：CLI の入力範囲の記述、英訳失敗時に元のエラーの対処が消えること、鍵の順序依存など 9 件を修正）。

## 2026-10-08 再レビューの指摘の修正（spec v1.7）

- 指摘の出典：[MCP再レビューと公開・更新方法の調査](research/202610081041_mcp_review_distribution_updates.md)。
- カタログの鮮度：全研究と種別ごとのエクスポートを別々に判定するようにした。`test_catalogue_freshness_is_judged_per_export`（全研究の CSV が古く registry の CSV だけ新しい状況を再現。修正前の判定では `current` になる）。同梱 DB（4 つとも 2026-10-01）は `current`、`snapshot_aligned=true`。
- 英訳の変更と実行中の抽出：`test_changed_translations_cancel_a_running_server_extraction`（取り消しを外すと失敗することを確認）。
- instructions：`test_instructions_state_every_rule_within_their_first_512_characters`。先頭 512 字に規則がすべて入り、全体は 1,418 字。
- 全体 397 passed（医薬品名の照合の変更を含まない状態）、ruff 合格。
- 医薬品名の照合（部分一致の廃止）は、実データの展開で悪化が 1 件あるため、別の変更として採否の判断を待つ。

## 2026-10-08 医薬品名の単語単位の照合（spec v1.7 の 4〜6）

- 方式の比較、悪化した例、残る限界と、レビューで判断してほしい点は [医薬品名の照合を単語の単位に変えることの評価と懸念](research/202610082148_medicine_name_matching.md) にまとめた。
- 方式の比較：カタログと辞書で同じ ATC コードを持つ全 553 組で比べた。塩の語を除いた完全一致は、aprepitant と fosaprepitant を分ける一方、表記の違いだけの正しい 12 組（insulin (human) と insulin human (rDNA)、botulinum toxin と type A／B など）も外した。単語の包含は、正しい組を保ったまま aprepitant の組だけを外し、表記の違いの 5 組を新たに結び付けた。EMA 側の成分の文字列もカンマで分ける案と、番号の一致を常に求める案は、正しい組（自家培養軟骨細胞、H5N1 の弱毒生ワクチン）を外したので採らなかった。
- 展開の変化：カタログと辞書の全 5,404 語のうち 30 語。独立の判定者（2 回）は、改善 17、同等 12、悪化 1 と判定した。悪化は制吐薬のクラス（A04）の所属薬から fosaprepitant が外れる件で、カタログが A04AD12 を aprepitant と名付けているため（v1.1 の規則どおり）。ローカル索引に fosaprepitant を挙げる研究は 0 件。辞書による製品名の展開（`drug_expansion`）は 1 語も変わらない。
- 潜在的な誤り（コードに関係なく、名前の異なる全組で同じ薬と判定されるもの）：旧規則 128 組、新規則 111 組。旧規則だけの 29 組（鏡像異性体、PEG 化体、プロドラッグ、別の INN）は新規則が分ける。新規則にだけ残るのは、ワクチンの成分の一部と全体（4 種混合と 6 種混合など）と、lutetium (177Lu) chloride とその結合体で、どちらも ATC が別のコードを付けているので結果に表れない。
- テスト：`test_names_correspond_by_whole_words_never_by_letters`、`test_separators_inside_brackets_do_not_split_a_medicine_name`（文字単位の照合、括弧の外だけの分割、番号の一致を外すとそれぞれ失敗することを確認）。

## 2026-10-08 カタログのスナップショット（版管理の計画の段階 1）

- 2026-10-01 の 4 つのエクスポートから `data-20261001` 用のスナップショットを作った。研究 3,314 件、種別の取り込みは claims 819、ehr 916、registry 559 件、補完の観測 432 件で、CSV の SHA-256 も含めてリポジトリの `data/ema.sqlite3` と一致した。DB は 44.2 MB、gzip で 12.9 MB。
- リポジトリの DB との比較：diabetes、heart failure、apixaban、registry、interstitial lung disease の 5 語で、返る研究と順序が一致した。
- `verify_snapshot` で展開と照合ができ、今の `refresh_from_bundle` で利用者の DB（取り込み日時を古くしたコピー）のカタログを置き換えられた。
- テスト：`tests/test_snapshot.py` 3 件（4 種のエクスポートからの作成と取り込み、種類の欠けと日付の混在の拒否、改ざんとスキーマ違いの拒否）。

## 2026-10-08 見出しの読み方の整合性（spec v1.8）

- 指摘の出典：[再々レビュー](research/202610082232_pr65_pr66_rereview_implementation_plan.md) の指摘 1。評価と方針は [評価と実装計画](research/202610082250_rereview_response_plan.md)。
- 再現と確認：同じ DB を共有する 2 つの `Service`（互いのタスクが見えない、別プロセスに相当）で、B が古い英訳で抽出を始め、A が英訳を直し、B が保存する順序を固定した。B の保存は `READING_CONTEXT_CHANGED` で拒否され、B の次の呼び出しは新しい英訳で抽出し直した（`test_another_server_cannot_save_an_analysis_read_under_old_translations`。保存時の照合を外すと失敗することを確認）。
- A→B→A で A のときに始めた抽出が保存できること、呼び出し側が古い読み方のバッチを保存できないこと、以前の `.headings` の取り込みと、読み方を記録していない英語の文書の解析がキャッシュとして使えることをテストした。
- 全体 405 passed、ruff 合格。OS の別プロセスを起動した検証はしていない（照合は SQLite の書き込みトランザクションで行うので、接続が別であれば同じ働きをする）。

## 未検証事項（継続）

- 外部LLM API呼出しの実認証検証は未実施。キーなしの呼出元LLM方式は合成PDFで保存・再利用まで検証。
- 仕様の10〜20プロトコルの人手精度評価、研究種別を跨ぐ5件の実PDF E2E評価は未実施。
- セクションの検出・関連箇所の選択はヒューリスティック。表の複雑なレイアウトや画像の定義は欠落する可能性がある。全文の恒久索引・OCR・意味的rerankingは含まない。
- 引用の照合は根拠の存在を検証するが、LLMの解釈・データソース使用状態・コードの意味を保証しない。
- 版番号が欠落／不正確な場合は `selection_reason` の不確実性を確認する。
- 複数プロセスで共有できるSQLiteを使うが、HTTPの2秒制限は各サーバープロセス単位。

## 人手評価の進め方

将来の検証対象として、記述研究、new-user cohort、active-comparator、SCCS、DUS、非DARWIN研究を含む10〜20件を選ぶ。各フィールドをCorrect / Partially Correct / Incorrect / Not Extracted、引用をCorrect / Incorrectで評価する。特にデータソース全件の再現、候補と使用予定の識別、疾患定義のコードと観察期間、PDF物理ページと印字番号の違いを記録する。
