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

## Codex App操作デモ（2026-09-28）

- プロジェクト設定の`ema-rwe` MCPをCodex Appから使用し、`catalogue_status`で有効なStudies CSV snapshot（3,312件）を確認した。
- 米国のピマバンセリン研究について`compare_protocols`でローカル候補1件を確認し、保存済みPDFの方法・患者定義・アウトカム・データソースを照合した。引用付き回答を保存後、`get_protocol_comparison`が`complete`を返し、比較表をCodex Appで開けた。
- 画面収録から53秒のMP4と106フレームのGIFを作成。FFprobeで両ファイルの形式・長さ／フレーム数を確認し、収録画面と比較表のフレームを目視確認した。GitHub上での埋め込み再生は未確認。

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
- 実装に関わっていない検証役の指摘（比較表が読み方を照合しない、質問別回答の鍵を保存時に計算する、最初の言語の判定が保存済みの英訳を上書きしうる、読み方の定義の食い違い、バッチの英訳の二度読み、ロックの誤認、使われない関数）をすべて直し、比較表、キャッシュの時刻更新の競合、質問別回答、stdio のスキーマのテストを加えた（照合を外すと失敗することを確認）。
- 全体 408 passed、ruff 合格。OS の別プロセスを起動した検証はしていない（照合は SQLite の書き込みトランザクションで行うので、接続が別であれば同じ働きをする）。

## 2026-10-08 医薬品名の関係、省いた所属薬、短い製品名（spec v1.9）

- 指摘の出典：[再々レビュー](research/202610082232_pr65_pr66_rereview_implementation_plan.md) の指摘 2、3 と短い製品名。
- 展開の変化：カタログと辞書の全 5,513 語（短い製品名を含む）のうち 163 語。独立の判定者は、広い名前をカテゴリー語に回した 2 語を改善 2、短い製品名で新たに展開された 109 語を改善 108、悪化 1 と判定した。残る 52 語は省いた所属薬が見えるようになっただけで、検索語は変わらない。
- 悪化の 1 件：`Riarify`。EMA の辞書の記録の成分が glycopyrronium を欠く（3 剤配合なのに `beclometasone / formoterol`）ため、2 剤配合の名前が加わる。main でも正式な製品名で検索すれば同じ結果になる辞書の誤りで、短い名前からも届くようになった。汎用の対策（成分名がコードのカタログ名と食い違う記録の成分名を使わない）は、遺伝子組換え第 VIII 因子の製品などを自分の成分名で検索できなくするので採らなかった。
- 省いた所属薬：全クラスで 86 組（判定者の数え方では名前とコードの組で 88 件）。判定者は 82 件を正当な所属薬（名前が ATC 名と違うだけ）、6 件を EMA の記録のコードの誤りと判定した。名前だけでは見分けられないので、自動では加えず、手順書で呼び出し側に判断させる。カタログはコードごとに名前を一つしか持たないので、カタログの名前で救える組は 0 件だった。
- 検索での確認：`pandemic influenza vaccine (H5N1) (live attenuated, nasal)` を `role=exposure` で `compare_protocols` すると、main では季節性の弱毒生ワクチンの研究（31683、28019 など）が具体語 `influenza, live attenuated` の一致として上位に並んだ。変更後は、この語がカテゴリー語になり、上位はクラス（J07BB）のカテゴリー語の一致になった。どちらも通信 0 件。
- テスト：`test_a_broader_catalogue_name_is_a_category_term_never_a_synonym`、`test_a_class_lists_the_members_it_leaves_out`、`test_a_current_product_name_finds_a_renamed_product_unless_it_is_ambiguous`（振り分けと別名の衝突の防止を外すと失敗することを確認）。全体 411 passed、ruff 合格。

## 2026-10-08 data-20261001 公開前の整合性監査

- 範囲：v0.5.4 以降の変更（PR #65〜#69）。読み取り専用のサブエージェント 3 体が、README と AGENTS.md、docs と仕様と設定の見本、プログラムが出力する文言をコードと照合した。スナップショットの作成と、出力の `--verify` による照合を、書かれたとおりに実行して確かめた（GitHub への公開と取り直しは、公開のときに記録する）。
- 直したコード：英訳の変更で抽出を取り消したときの文言、`reading` の説明（空文字でも渡す）、`expand_medicine` の説明（狭い名前は加えない）、manifest の観測の数え方（そのスナップショットの研究に限り、医薬品を補った研究の数 `backfilled_studies` を加えた）、manifest の欠けを `SNAPSHOT_INVALID` にする、スクリプトの説明の改行と `--verify`（公開したファイルの照合）。
- ユーザーの判断で変えた挙動：エクスポートの日付のずれ（`snapshot_aligned=false`）は `snapshot_note` で知らせるだけにし、再取得は期限切れのときだけ勧める（spec v1.9 の 5）。AGENTS.md の「公式エクスポートは 1 回」と食い違い、全研究の CSV だけを新しくした利用者に再取得を勧め続けるため。
- 直した文書：README（英訳の保存先、EMA の辞書のコードの使い方、広い名前と省いた所属薬、再取得の条件）、README_DEV（モジュールの役割、`catalogue_status` と `medicine_expansion` の項目、同梱 DB の差し替えが触れない表、スナップショットのリリース、日付の接頭辞）、AGENTS.md、docs（`reading` の受け渡し、名前の比べ方、リリース手順の `--notes` と照合のコマンド）、spec v1.8 の参照番号、計画書、`.env.example`。
- テスト：全体 413 passed、ruff 合格。
- 公開：最終の main（2846627）でスナップショットを作り直し（研究 3,314、観測 432、補完 215、gzip 12.9 MB）、`data-20261001` として `--latest=false` で公開した。`gh release download` で取り直したファイルを `--verify` で照合できた。アプリの最新版の表示は v0.5.4 のまま。

## 2026-10-09 探索の読み方の固定、省いた所属薬の全件、種別ごとの取り込みの記録（spec v1.10）

- 指摘の出典：外部の再々々レビュー（9d69c3d）の 3 点。実装方針（同じ内容の別種別の CSV を拒否する案）にはレビュワーが反対し、種別は排他的でなく正当な一致がありうるとの指摘を受けて、記録を内容と種別ごとに残す方式に改めた。
- 探索：内部 LLM の探索ループで開始後に別の英訳へ変えても、`search`、`outline`、`read` の応答と保存した回答の `source.reading` が開始時の値で、開始時の鍵の回答だけが保存されることを確かめた。呼出元の探索ツールの応答は、変更後の読み方を名乗る。各ツールに期待する読み方を渡して拒否させる仕組みは無く、呼出元が探索中の A→B→A を見落とせば、保存時の照合をすり抜ける（既知の制限）。
- 省いた所属薬：同梱 DB で VACCINES 54 件、VIRAL VACCINES 44 件、Influenza vaccines 22 件が、`compare_protocols` の `medicine_expansion` に全件、`omitted_members_total` と同数で届いた（応答は 17〜42 KB）。
- 検索の順位：VACCINES、VIRAL VACCINES、Influenza vaccines、Blood coagulation factors、apixaban（`role=exposure`）で、main と変更後の候補の順序、facets、検索語、カテゴリー語が一致した。
- 取り込みの記録：実際の 20261001 の 4 つのエクスポートでスナップショットを作り直すと、manifest のエクスポートのファイル、件数、SHA-256 と研究数が公開済みの `data-20261001` と一致した（DB は取込日時を含むので SHA-256 は一致しない）。claims の内容を ehr に、全研究の内容を registry に複製した 4 つでも作成でき、件数は claims と ehr が 819、全研究と registry が 3,314、`catalogue_status` は 4 種類とも記録があり `missing_source_type_exports` が空になった。修正前はこの入力で manifest の作成が `KeyError` で止まった。
- テスト：`test_server_exploration_reads_and_saves_under_the_translations_it_started_with`、`test_client_exploration_responses_name_the_reading_they_used`、`test_every_omitted_class_member_reaches_the_response`、`test_identical_exports_of_different_kinds_each_keep_their_import_record`。スナップショットのテストの入力は、別種別のバイト単位で同じ CSV に改めた（以前は内容を変えて衝突を避けていた）。修正を外すと、新しいテストとスナップショットのテストが失敗することを確認した。

## 2026-10-09 v0.5.5 のリリース前の整合性監査

- 範囲：v0.5.4 以降の変更（PR #65〜#72）と版の表記。読み取り専用のサブエージェント 3 体が、README と AGENTS.md と CHANGELOG、docs と仕様と設定の見本、プログラムが出力する文言とパッケージの中身を、コードと照合した。誤りは 0 件、不足 4 件、軽微 14 件（重複を除く）。
- ユーザーの判断で変えた挙動：CLI の `import-csv` は、種別の CSV でも研究にタグを付けないのに、`catalogue_status` はファイル名から種別を取り込み済みと表示していた（v0.5.4 以前から）。ファイル名の種別でタグを付けるようにした（`test_cli_import_csv_tags_a_typed_export_by_its_file_name`。修正を外すと失敗する）。検証役の指摘で、MCP の `import_catalogue_csv` と `import-all` も `source_type/` の外に置いた種別の名前の CSV にタグを付けず、同じ食い違いがあると分かったので、どのフォルダでもファイル名の種別でタグを付けるようにそろえた（ユーザーの判断「経路をそろえ、表示とデータを一致させる」の範囲）（`test_a_typed_export_outside_source_type_is_still_tagged_by_its_name`。修正を外すと失敗する）。
- 直したコード：`exploration_limit_reached` に `reading` を加えた（英語以外の文書で、呼出元が `cache_protocol_answer` に渡す値が無かった。`test_autonomous_loop_is_bounded_and_does_not_cache_unfinished_answer` で確かめる）、比較表の指示に `reading` を渡すことを加えた、`check_protocols` の説明の余分な語、取り込みのエラー文言のフォルダ名。
- 直した文書：リリース手順（初回公開の節を実施済みの記録と明記、版を上げる箇所の一覧、同梱カタログの更新、監査のフック、公開後の確認の版と `stale` の扱い）、README（第 4 レベルのクラスを加える条件）、README_DEV（`snapshot_count`、旧形式の取り込みの記録、探索ツールの `source.reading`、`research_protocol` の `reading`）、手順書（役割で順位を付ける列）、spec v1.10 の `snapshot_count` の増え方、exploration.md の題の版。
- パッケージ：`uv build` の wheel に、同梱 DB、医薬品辞書、手順書が入ることを確かめた。

## 2026-10-09 v0.5.5 の公開と実利用での確認

- 公開：タグ `v0.5.5` はマージ後の main（`f962ca7`）を指し、GitHub の最新版の表示は v0.5.5（`data-20261001` はアプリと別のリリースのまま）。資格情報なしで `uvx --from git+https://github.com/parfait0707/ema-rwe-mcp@v0.5.5 ema-rwe --help` が動き、パッケージの版は 0.5.5 だった。
- ユーザーの確認（PR #73 の動作確認）：`uvx` での起動と、`.mcp.json` を v0.5.5 にして `/mcp` で再接続したときのツール数 18。
- 実利用（「2 型糖尿病患者を対象とした GLP-1 を含む研究の組入・除外基準」、Claude Code から MCP で実行）：
  - `catalogue_status` は `current`（4 つのエクスポートとも 2026-10-01、`snapshot_aligned=true`）。`refresh_drug_dictionary` で医薬品辞書を更新した（2026-10-09 時点の EMA の 2,746 記録から 2,256 記録を保持、通信 1 件）。
  - `compare_protocols`（2 型糖尿病と GLP-1 受容体作動薬の 2 ブロック）は 56 件で `needs_narrowing`。ユーザーの選んだ種別（claims、registry）と実施国（日本、デンマーク）で 6 件になり、`check_protocols=6` で公開プロトコルのある 4 件（49324、49276、44917、49285）が分かった（通信 30 件、PDF の取得なし）。ユーザーがこの 4 件を選んだ。
  - 4 件とも英語でテキスト層のある PDF（`reading` は空）。呼出元の抽出と質問別回答を sonnet のサブエージェント 4 体が並行して行い、比較表の 4 行とも `complete` になった。引用のページの誤りやページをまたぐ引用（`EVIDENCE_INVALID`）、本文に無いデータソース名は保存時に拒否され、担当が読み直して保存し直した。
  - 担当の 2 体が「後のバッチで省いた単一の項目が消えた」と報告した。コードでは、途中のバッチの統合（`llm.merge_into`）は空の項目を飛ばすので消さない。一方、`coverage_complete=true` の保存で途中のバッチは捨てられるので、保存の後に再び `coverage_complete=true` で一部だけを渡すと、解析全体がその内容で置き換わる。仕様どおりの挙動として README_DEV に注意を書いた。
  - 担当の 1 体が動作確認のため、49285 の PDF に質問 `__canary_test__` の回答を保存した。本来の質問の回答とは別の鍵で、比較表には出ない。回答を消すツールは無い。

## 2026-10-09 Windows でのテストの CSV の改行

- 外部の指摘：Windows で `tests/test_snapshot.py` の `test_snapshot_is_built_from_the_four_exports_of_one_refresh` が失敗する。テストの CSV（`conftest.csv_file`）は `csv.writer` が行末を `\r\n` にし、`write_text` の改行の変換がさらに `\r` を足して `\r\r\n` になる。`splitlines()` の 2 行目が空になり、`lines[:2]` で作る ehr と registry の CSV に研究が入らない（アプリの取り込みの不具合ではない）。
- 修正：`csv.writer` で作るテストの CSV（`conftest.csv_file`、`test_core`、`test_screening` の 3 か所）を `newline=""` で書き、改行を変換しない。件数の検証は変えていない。
- 確認：Linux では変換が起きないので、`newline="\r\n"`（Windows と同じ変換）に一時的に替えて同じ失敗を再現し、修正で通ることを確かめた。全体 419 passed。Windows の実機では実行していない。

## 未検証事項（継続）

- 外部LLM API呼出しの実認証検証は未実施。キーなしの呼出元LLM方式は合成PDFで保存・再利用まで検証。
- 仕様の10〜20プロトコルの人手精度評価、研究種別を跨ぐ5件の実PDF E2E評価は未実施。
- セクションの検出・関連箇所の選択はヒューリスティック。表の複雑なレイアウトや画像の定義は欠落する可能性がある。全文の恒久索引・OCR・意味的rerankingは含まない。
- 引用の照合は根拠の存在を検証するが、LLMの解釈・データソース使用状態・コードの意味を保証しない。
- 版番号が欠落／不正確な場合は `selection_reason` の不確実性を確認する。
- 複数プロセスで共有できるSQLiteを使うが、HTTPの2秒制限は各サーバープロセス単位。

## 人手評価の進め方

将来の検証対象として、記述研究、new-user cohort、active-comparator、SCCS、DUS、非DARWIN研究を含む10〜20件を選ぶ。各フィールドをCorrect / Partially Correct / Incorrect / Not Extracted、引用をCorrect / Incorrectで評価する。特にデータソース全件の再現、候補と使用予定の識別、疾患定義のコードと観察期間、PDF物理ページと印字番号の違いを記録する。

## 2026-10-09 Codex Appの4例とREADME整理

- ユーザー指定により、MCP実行時間を「各検索開始から最終比較出力までの経過時間−絞り込み質問の待機時間」と定義。元の時刻から再計算し、順に1016.761秒（16分57秒）、124.351秒（2分04秒）、442.475秒（7分22秒）、460.341秒（7分40秒）をREADMEへ掲載した。[条件・算式](benchmarks/20261009-codex-app.md)と[計測JSON](benchmarks/20261009-codex-app.json)を追加。呼出元抽出、各質問1回、別ケースとの交差処理、ケース3・4の解析共有を明記した。サーバー内のツール実行時間合計と料金は未計測。
- READMEの架空の出力例と過去の時間・料金表を今回の実測例へ差し替え、詳細な検索ロジックを[別文書](search-logic.md)へ移した。README_DEVのモジュール説明・ツール引数・仕様履歴の重複を削減し、設定条件、全件処理、保存済み解析の置換に関する注意を残した。既存のCodex Appデモを保持した。
- 検証：変更した4つのMarkdownのローカルリンク・見出しアンカー54件、コードフェンス、UTF-8、時刻差・差引・丸め4件、MCP公開18ツールとREADME_DEVの名称一致を確認。`git diff --check`は合格。
- ビルド：環境のuvは`uv build`と`uv run -m`に未対応。代替の`uv run --with build --with hatchling python -m build --no-isolation`でsdistとwheelを生成できた。
- 文書・計測記録のみの変更で、アプリコードとMCPスキーマは変更していない。pytest・ruffと新たな研究検索・PDF再解析は未実施。外部リンクの到達性と画面描画は未検証。

## 2026-10-09 Codex App操作デモの差し替え

- 新規チャットの初回入力を「2型糖尿病×GLP-1×デンマークの研究を探し、見つかったらinclusion criteriaを教えてください。レジストリー研究に絞って調査してください。」にし、実際のema-rwe MCP呼出しと回答画面を収録した。録画を依頼するメタ入力は含めていない。
- `catalogue_status`、`plan_study_search`、`compare_protocols`、`analyze_protocol`、`research_protocol`、`get_protocol_comparison`の実行を画面で確認。ローカル候補4件中、3件の公開プロトコルから組入れ基準を回答し、プロトコルのない1件も表示した。保存済み解析を利用するデモであり、先の4例の時間計測とは別の実行である。
- 通常のGDIウィンドウ録画は黒画面になったため採用せず、Windows.Graphics.Captureによる実画面キャプチャを約2秒間隔で保存し、64枚を時系列で動画化した。待機部分を短縮し、入力と最終回答の表示時間を確保。サイドバーと右側の別パネルは切り取った。
- `docs/media/codex-ema-rwe-demo.mp4`は1060×1320、63.8秒、319フレーム。GIFは900×1121、64.0秒、128フレーム。FFprobeと入力・MCP履歴・最終回答の抽出フレームで形式、長さ、画面内容を確認した。GitHub上の埋め込み再生は未確認。
- READMEのデモ説明を今回の入力・カタログ種別`registry`へ更新。接続に使用したプロジェクト設定と収録中間ファイルはGit管理外。コードとMCPスキーマは変更していない。

## 2026-10-09 開発ガイドのMermaid図復元

- 以前のREADMEにあった処理全体と検索語展開のMermaid図を、README_DEVの「データ処理の流れ」「内部の探索ロジック」に復元。候補数を数えるノードは、検索語ではなく候補研究の重複除去であることが分かる表現に修正した。
- 文書のローカルリンク・見出しアンカー55件、コードフェンス、UTF-8、既存の計測値・ツール名の整合性と`git diff --check`を確認。コード変更はなく、Mermaidの実レンダリングは未確認。

## 2026-11-01 操作デモGIFのモザイク加工

- `docs/media/codex-ema-rwe-search.gif`の冒頭7.5秒のプロンプト入力画面を削除。元録画は入力画面から処理中の回答へ直接切り替わるため、最初の送信後フレームの回答領域を背景で隠した「送信済みプロンプトのみ」の画面を1秒追加した。
- 処理中のツール名・読込ファイル表示、結果表の引用名・研究ID、本文の引用リンクと調査記録リンクをモザイク化。表の組入れ基準と説明本文は保持した。MP4は変更していない。
- Pillowで保存後に全フレームを読み込み、900×1121、97フレーム、57.5秒、無限ループを確認。FFmpegで全フレームのデコードが成功し、FFprobeでも寸法・フレーム数・長さが一致した。冒頭・処理中・表の描画途中・最終結果を抽出して目視確認した。
- メディアと検証記録のみの変更のためpytest・ruff・ビルドは未実施。GitHub上の再生と公開反映は未確認。
- 追加依頼により、最終回答の網羅性に関する注記内の`EMA`も描画途中からモザイク化。処理中の`Ema Rwe`表示と`Catalogue status`は先のモザイク範囲内にあることを確認した。再保存後も97フレーム・57.5秒・無限ループを維持し、最終回答の拡大画像で追加範囲を目視確認、FFmpegで全フレームをデコードできた。
- 再確認の依頼を受け、「候補数はEMA全体の網羅件数ではありません。」の折返し部分を含む文全体へ範囲を拡大。再保存した97フレームすべてで当該2領域が文字を保持しない粗いブロックになっていることを画素検証し、最終フレームを拡大して目視確認した。全フレームのFFmpegデコードも成功。旧表示との混同を避ける確認用コピーをローカルの`tmp/gif-edit-review/codex-demo-redacted-v2.gif`へ保存した。

## 2026-11-03 2026-11-02エクスポートの取り込みとプロトコル列名の変更

- 2026-11-02のStudies exportは文書の列名が変わった（`Protocol file(s)`・`Protocol file(s) - URI`・`Protocol URL`が無くなり、`Initial protocol file - URI`・`Updated protocol file(s) - URI`が加わった。題名・医薬品・国・種別などの列は不変）。旧コードはこの版から取り込むと全研究の`protocol_listed`を`null`にし、`backfill-protocols`の対象が0件になった。`storage.import_csv`に新しい2列を加え、回帰テスト`test_import_reads_the_protocol_columns_of_the_november_2026_export`を追加。再取り込み後の`protocol_listed`はtrue 2,150件・false 1,193件（2026-10-01版はtrue 2,037件）。
- `import-all`後の同梱DBは3,343研究（claims 824、ehr 926、registry 575、タグなし1,515）、`catalogue_status`は4種とも`current`、`snapshot_aligned=true`。
- 作業用DBの`backfill-protocols`は、医薬品辞書を`EMA_DB_PATH`の隣から読むため、辞書を置かない場所では題名の医薬品名を多く見落とす（題名から補えた研究122件、辞書を指定すると191件）。`EMA_DRUG_DICTIONARY_PATH=data/ema-medicines.json`を指定して実行し、README_DEVの手順に追記した。`merge-observations`は観測を消さないので、辞書なしの試行で同梱DBが失われたものはないことを照合した。
- 通信ありの補完：医薬品欄が空の716研究のうちプロトコルのある未読25件を取得、7件でPASS情報表から医薬品を補完、1件は`PDF_OCR_REQUIRED`、429や障害による停止なし。同梱DBで医薬品を補えた研究は214件→219件、観測455件、`integrity_check` ok。
- `uv run pytest -q` 420 passed、`ruff check`・`ruff format --check` green。
- data-20261102の公開前の整合性監査：`consistency_scan.py`で古い版の記述・壊れた参照なし。プロトコル列の記述を照合し、README_DEVの`protocol_listed`の説明に11月版の列を追加（spec v1.1は版付きの記録として変更しない）。スナップショットは`scripts/build_catalogue_snapshot.py 20261102`で作成（3,343研究、観測455件、補完あり221件）。pytest・ruff・`uv build`はgreen。
- v0.5.6の公開前監査：版の表記（pyproject・uv.lock・README・README_DEV・`config.py`・`.env.example`・`.mcp.json.sample`・`.codex/config.toml.sample`・CHANGELOG）を0.5.6に揃え、`consistency_scan.py`の残りは日付付きの調査記録1件のみ。pytest 420 passed、ruff green、`uv build`のwheelに`ema_rwe/data/ema.sqlite3`（44,974,080 bytes、2026-11-02版）と`mcp-workflow.md`が入ることを確認。

## 2026-11-19 途中バッチの保存、読み残しの検出、抽出と質問回答の一括化（spec v1.11）と方式の比較

- 目的：呼出元抽出の所要時間を縮める 2 案を、実プロトコルで現行方式と比べ、どちらを採るかを決める。案 A は引用を行番号で指定する（サーバーが逐語引用に展開する）。案 B は抽出と質問回答を 1 回で行う（`analyze_protocol(question)`）。
- 方法：
  - 研究 49324、49276、49285、105807 の最新プロトコル（108、70、69、82 ページ。読む節は 10.8 万〜17.5 万字）を 1 件ずつ取得した。初回は 4 件続けて取得して 2 件目で 429 になり、7 分待って 30 秒間隔で取り直した。
  - 呼出元の役は Claude Code のサブエージェント（Sonnet）。CLI を包むスクリプトが方式ごとの引数を足し、全呼び出しの時刻・終了コード・提出したファイルの大きさを記録した。エージェントへの指示文は方式によらず同じ。質問は「組入れ基準と除外基準は何か」。
  - 方式は現行、A、B、A+B の 4 つ。各実行は別の DB の複製を使い、PDF と HTTP キャッシュは共有した。
  - 正解は、各プロトコルの適格基準の節を読んで作った項目の一覧（13、16、10、10 項目）。回答の事実の値か引用が項目の語句に一致すれば「一致」、同じ事実の逐語引用が一致すれば「引用で裏づけ」と数えた（正規表現による自動判定。低い値は目視で確かめた）。
- 1 件目（49324、各方式 2 回）は比較に使わない：エージェントへの指示文に質問を書いたため、8 回のうち 5 回が「質問は適格基準だけ」としてデータソースを抽出しなかった。指示文から質問を除き、全項目の抽出を明記して、残り 3 件を実行した。回答は 8 回のうち 7 回が 13 項目すべてに一致した。
- 1 件目で見つかった不具合：8 回のうち 2 回が、次の offset を誤って途中のバッチ（節 104〜118 など）を読まずに `coverage_complete=true` を呼び、サーバーはそのまま保存した。また CLI は呼び出しごとに別プロセスなので、メモリ上の途中バッチは結合の時点で失われていた。この 2 点を先に直した（途中バッチの DB 保存、読み残しの検出）。3 件目の 1 回で、読み飛ばしが `INCOMPLETE_EXTRACTION` で止まり、読み直して完了した。
- 2〜4 件目の結果（各方式 3 回の合計か平均。所要時間は最初の呼び出しから最後の呼び出しまで）：

| 方式 | 所要時間（分） | ツール呼び出し | トークン（千） | 検証エラー | 一致（36 項目） | 引用で裏づけ |
|---|---:|---:|---:|---:|---:|---:|
| 現行 | 4.6 | 24.7 | 193 | 16 | 36 | 33 |
| A（行番号） | 4.8 | 26.0 | 176 | 23 | 36 | 36 |
| B（1 回で、初版） | 3.9 | 19.0 | 168 | 11 | 33 | 23 |
| A+B | 3.5 | 23.0 | 168 | 21 | 36 | 36 |
| B（最終版） | 4.8 | 19.0 | 179 | 8 | 36 | 35 |

- 所見：
  - A は所要時間を縮めなかった。検証エラーは増えた（1000 字を超える行の範囲、8 字に満たない行、データソース名が引いた行に無い）。引用は平均 240〜290 字で、逐語引用（62〜84 字）の 3〜4 倍になった。引用が長い分、裏づけの判定は満点になった。
  - B の初版は呼び出しが 2 割少なく、所要時間も短かった。ただし回答が少数の事実にまとめられ（1 事実の引用は 8 件まで）、項目ごとの引用が減った。自動判定で不一致の 3 項目は、目視では 2 項目が値に書かれており、1 項目は「decision tree による組合せ」とだけ要約されていた。
  - B の最終版は「1 項目を 1 事実にし、その引用を付ける」と指示に加えた。一致と裏づけは現行と同じかそれ以上になったが、所要時間の短縮は消えた（4.8 分。現行 4.6 分）。呼び出しの数と検証エラーは少ないまま。
  - 検証エラーの大半は、引用の写し間違いではなく「データソース名が引用に現れること」の規則によるもので、どの方式でも起きた。
  - 結論：所要時間は、バッチを読んで抽出を書く部分でほぼ決まり、質問回答を別に読む分（最大 4 万字）は小さい。A は採らない。B は精度を落とさずに往復を減らすので採るが、所要時間の短縮は確認できていない。
- 追加の計測（4 件の PDF）：`extract_pages` は 1 回 1.0〜1.3 秒、`sections` は 0.1〜0.7 秒。現状は探索と保存の呼び出しごとに解析し直している。読む節の本文は、連続する空白と空行を詰めると 6〜19% 短くなる。
- `uv run --extra dev pytest -q` 425 passed、1 skipped。`ruff check`、`ruff format --check` 合格。`uv build` で 0.5.7 の sdist と wheel を生成できた。実 stdio の MCP テストで `analyze_protocol` の `question`、`cache_protocol_analysis` の `answer` と `question` がスキーマにあることを確認した。
- 限界：
  - 各方式 3 件 × 1 回で、方式の差は実行ごとのばらつき（同じ方式でも 1 分以上）より小さい。所要時間の順位は確定していない。
  - 呼出元は CLI 経由のサブエージェントで、実際の MCP クライアントでの往復ではない。最大 8 本を同時に走らせたので、所要時間は同時実行の影響を受けている（最終版の 3 回は 3 本同時）。
  - 質問は 1 種類（適格基準）。回答が抽出の対象外の節（付録など）にしかない質問で、B が `research_protocol` より見落とすかは測っていない。
  - 正解は実装者が作り、判定は正規表現による。誤った内容の有無（適合率）は系統的には採点していない。
  - 英語以外のプロトコル、内部 LLM の経路では実行していない（内部 LLM の経路は変えていない）。
- v0.5.7 の版の表記：`pyproject.toml`、`uv.lock`、`README.md`、`README_DEV.md`、`src/ema_rwe/config.py`、`.env.example`、`.mcp.json.sample`、`.codex/config.toml.sample`、`CHANGELOG.md` を 0.5.7 に揃えた。残る 0.5.6 の記述は、日付付きの記録（CHANGELOG の過去の節、検証記録、調査記録、計測記録）だけ。影響を受ける文書として `docs/mcp-workflow.md`、`docs/comparisons.md`、`docs/exploration.md`、`README.md`（質問の仕方と流れの 4）、`README_DEV.md`（呼出元抽出の説明と注意点）を更新した。同梱 DB は変えていない。

## 2026-11-19 `service.py` の分割（動作を変えない移動）

- 1,762 行の `service.py` を、コードを書き換えずに 6 つのモジュールへ移した。`Service` は入口として残し（100 行）、メソッドの本体は `catalogue.py`（カタログの状態・取込・補完）、`screening.py`（検索・候補の集計・検索語の計画）、`protocols.py`（研究ページとプロトコルの取得・PDF の読取）、`extraction.py`（抽出・見出し英訳・質問別回答の保存）の mixin に置く。未一致語の記録は `unmatched.py`。
- 移動はスクリプトで行い、関数と定数の範囲を AST から取って本文をそのまま写した。移動の前後で、58 個の関数・メソッドと全定数の AST が一致することを確かめた（書式と位置を除いて同一）。`Service` の公開メソッドは 25 個のままで、引数も変わらない。
- 変えたのは import と、テストの差し替え先 3 種だけ：`ema_rwe.service.extract_with_provider` → `ema_rwe.extraction.extract_with_provider`、`ema_rwe.service.complete_json` → `ema_rwe.screening.complete_json`、`ema_rwe.service.MAX_NAMES` → `ema_rwe.screening.MAX_NAMES`。`ema_rwe.service` からの `Service` と `source_type_from_filename` の import は従来どおり使える。
- `uv run --extra dev pytest -q` 425 passed、1 skipped（実 stdio の MCP テストを含む）。`ruff check`、`ruff format --check` 合格。`uv build` で wheel に新しい 5 モジュールが入ることを確認した。
- 残したもの：12 個前後の位置引数（`repo.search`、`_union`、`_match_scope`）を 1 つのモデルにまとめる変更は、動作を変えない移動と分けるため行っていない。mixin はどれも `Service` の属性（`settings`、`repo`、`client`、`archive`、`explorer`）を前提にしており、単独では使えない。

## 2026-11-19 解析済み PDF の再利用、読み手向け本文の空白、データソース名の確認、ツール呼び出しの計測（spec v1.12）

- 計測の方法：`scripts/time_tools.py 49324 49276 49285 105807`。LLM を使わず、1 研究分のツール呼び出し（全バッチの `analyze_protocol` と `cache_protocol_analysis`、章一覧、検索、読取、`research_protocol`）を同じプロセスで順に実行する。PDF と HTTP の応答は取得済みのものを使い、通信はしていない。

| 呼び出し | 変更前の平均（ms） | 変更後の平均（ms） |
|---|---:|---:|
| `analyze_protocol`（最初のバッチ） | 2,547 | 1,629 |
| `cache_protocol_analysis`（バッチ） | 1,545 | 49 |
| `analyze_protocol`（次のバッチ） | 1,481 | 38 |
| `get_protocol_outline` | 1,480 | 10 |
| `search_protocol_text` | 1,475 | 40 |
| `read_protocol_text` | 1,427 | 9 |
| `research_protocol` | 1,556 | 67 |

- 4 研究の合計は 85.5 秒から 8.4 秒になった。変更後は PDF の解析が 4 回（研究ごとに 1 回、計 5.4 秒）で、49 回は解析済みのページを再利用した。変更前は呼び出しのたびに解析していた。
- 空白を詰めた結果、読む節の本文は 529,273 字から 467,381 字（12% 減。研究ごとに 5〜18%）、バッチは 19 から 17 になった。空行だけを詰めても 0〜1% しか減らず、連続する空白を 1 つにしても 2 つに詰める場合より 1 ポイントしか減らないので、列の切れ目が残る「3 つ以上を 2 つ」にした。
- データソース名の検証エラーの内容：方式の比較（前の節）では、エラーの大半がこの規則によるものだった。記録したエラーと呼出元の報告から、原因は 3 つに分かれた。
  - 表で名前が行をまたぎ、別の列の値が間に入る（49276 の p.32：`Income and education registries from     -` の次の行に `Statistics Denmark`）。逐語の一致では通らず、呼出元は名前を途中で切って保存していた（`Income and education registries from`、`Norwegian Registry for Primary`）。
  - 引用が使い方や定義の文で、名前は別の箇所にある。プロトコルは名前を一度書いた後、略語で続ける（105807 の `Hospital Episode Statistics (HES)` は p.44 と p.47、引用は p.48 など）。
  - 呼出元がプロトコルに無い語を足した（`SDRN-EPI (Scotland)`）。これは拒否するのが正しい。
- 規則を 2 段階で直し、それぞれ実プロトコルで確かめた（呼出元は Sonnet のサブエージェント、1 回方式）。
  - 第 1 版（単語の並びで照合し、引用したページに名前があれば引用を足す）：3 研究で検証エラー 7 件、うち名前によるものは 4 件。4 件とも、名前が引用したページに無く、別のページにあった（`Hospital Episode Statistics (HES)`、`Office for National Statistics (ONS) mortality data`、`CPRD Aurum`、`Danish National Health Registers`）。正解 36 項目はすべて一致し、引用の裏づけも 36。
  - 第 2 版（本文全体から探す。目次、参考文献、背景、チェックリストを除く）：上の 4 件を実 PDF で再現すると、すべて最寄りの方法の章から名前を含む引用が足されて通り、語を変えた名前（`Danish National Health Registries`、`CPRD Aurum database of Scotland`）は拒否された。2 研究の実行（105807、49285）で名前によるエラーは 0 件。データソースと種別判定の計 84 件のうち、31 件にサーバーが名前を含む引用を足した。
- 残った検証エラー（第 2 版の 2 研究で 4 件）：8 字に満たない引用（`None`、`I42.1`）が 2 件、ページ番号の誤りが 1 件、1 つの事実に引用が 9 件以上が 1 件。今回は変えていない。
- `uv run --extra dev pytest -q` 445 passed、1 skipped（追加 20 件：同じ PDF の解析が 1 回であること、読み方ごとの別扱い、節の複製、失敗した解析を保たないこと、同時の要求が 1 回の解析になること、古い PDF の追い出し、詰めた本文から写した引用の検証、名前の単語照合、同じページと別のページからの引用の追加、参考文献だけにある名前の拒否、呼出元のバッチの保存、ツール呼び出しの記録）。`ruff check`、`ruff format --check` 合格。実 stdio の MCP テストは通り、記録が stdout に出ないことを確かめている。
- 限界：
  - キャッシュはプロセスのメモリだけなので、CLI は呼び出しごとに 1 回は解析する。8 件を超える PDF を行き来すると解析し直す。
  - 所要時間の計測は 1 台・各 1 回で、LLM の読む時間を含まない。呼出元抽出の全体の所要時間（1 研究 3〜5 分）に対して、短くなるのはツールの待ち時間（1 研究あたり約 20 秒から約 2 秒）である。
  - 実プロトコルでの確認は第 1 版 3 件、第 2 版 2 件で、各 1 回。空白を詰めたことで表の読み取りが変わるかは、適格基準の正解（36 項目すべて一致）でしか見ていない。コード表の列の対応は採点していない。
  - 名前の単語の間に入ってよい語は 3 つまでという値は、見た表の例から決めた経験則である。離れた語を拾って別の名前を通す誤りは、実プロトコルでは見ていないが、系統的には測っていない。
  - サーバーが足した引用は名前の書かれた箇所であり、その事実の使い方や定義を支える引用ではない。足した引用かどうかは保存した解析では区別していない。
  - 内部 LLM の経路は、本文の空白の詰めと単語の照合だけが変わる。実プロバイダでは実行していない。

## 2026-11-19 引用のページの訂正、サーバーが手を入れた引用の印、引用の下限と上限の案内（spec v1.13）

- v1.12 の確認で残った検証エラーは 3 種類だった：8 字に満たない引用（`None`、`I42.1`）、ページ番号の誤り（12 を 13、27 を 28 など）、1 つの事実に引用が 9 件以上。ページの誤りはサーバーが直し、残りの 2 つは下限と上限を指示とスキーマの説明に書いた。
- 実 PDF での再現：49285 の p.12 の 1 行を p.13 として渡すと、p.12 に直り（`server=page`）、検証に通る。
- 実プロトコルでの確認（49285 と 49276、呼出元は Sonnet のサブエージェント、1 回方式、各 1 回）：
  - 検証エラーは 2 研究で 1 件（行の途中で切った引用が逐語でなかった）。8 字未満、引用 9 件以上、ページ番号の誤り、データソース名によるエラーは 0 件。v1.12 の版での同じ 2 研究は 4 件、その前の版は 4 件だった。
  - 保存した解析の引用 468 件のうち、サーバーがページを直したものが 10 件（`page`）、名前の箇所として足したものが 15 件（`source_name`）。質問別回答の引用 64 件には印が無かった。
  - 正解（適格基準 26 項目）はすべて一致し、引用の裏づけも 26。最初から最後の呼び出しまでは 3.3 分と 3.5 分（v1.12 の版では 6.6 分と 4.4 分。各 1 回で、同時に走らせた本数も違うので、差の大きさは確定していない）。
- `uv run --extra dev pytest -q` 449 passed、1 skipped（追加 4 件：最も近いページへの訂正と、どこにも無い引用・前後に同じ距離の引用を直さないこと、印と比較表のページの表示、呼出元のバッチでの訂正と指示の文言）。既存の 1 件は期待を改めた（別のページの逐語の引用は、拒否ではなく訂正して保存する）。`ruff check`、`ruff format --check` 合格。実 stdio の MCP テストは通る。
- 計測：`scripts/time_tools.py` で `cache_protocol_analysis` は 1 回 49 ms から 61 ms になった（全ページの本文を正規化して引用を探す分）。
- 限界：
  - ページの訂正は、同じ文が複数のページにあるとき最も近いページを選ぶ。ページのヘッダーや定型文のような繰り返しの引用では、呼出元が意図したページと違うことがありうる（実プロトコルでは見ていない）。
  - 印は今回以降に保存した解析に付く。以前の解析の引用は印が無く、サーバーが足したものかどうかは分からない。
  - 引用の下限と上限の案内の効果は、2 研究で違反が 0 件だったことでしか見ていない。
  - 比較表でページを分けて示すのは種別判定の列だけで、データソースの列はもともとページを示していない。

## 2026-11-19 push 前のチェックの一本化（ホスト型の CI を使わない）

- `scripts/check.py` が、版の表記の一致、`uv.lock`、`ruff check`、`ruff format --check`、`pytest`、`uv build`、wheel の中身を順に確かめる。最初の失敗で止まり、段階の名前を表示する。`.githooks/pre-push` が push の前にこれを実行する（`git config core.hooksPath .githooks` を clone ごとに 1 回）。GitHub Actions などのホスト型の CI は使わない（利用者の方針）。
- 開発用のツールを `dev` の extra から `dev` の依存グループへ移した。`.venv` を消した状態で `uv run pytest -q` が 449 passed、1 skipped（以前は `--extra dev` が無いと `httpx` が見つからず失敗した）。`uv.lock` は、このプロジェクトの項目だけを書き換えた。新しい uv で `uv lock` を実行すると全パッケージに `upload-time` が加わり 1,400 行が変わるため、依存の版が 1 つも変わっていないことを確かめたうえで、その書式の変更は取り込んでいない。`uv lock --check` と `uv run --locked` は通る。
- 実行結果：全 7 段階で 21 秒（うち pytest 14 秒）。`.mcp.json.sample` の版を 0.5.6 に戻すと `versions` の段階で止まり、ファイル名と期待する文字列を表示することを確かめた。wheel の段階は、`src/ema_rwe/` の全モジュールと、カタログ DB、医薬品辞書、手順書が入っていることを見る。
- hook を有効にした clone から push し、チェックが走ってから push されることを確かめた。
- 限界：
  - hook は clone ごとに有効にする必要があり、強制はできない。有効にしていない clone や `--no-verify` の push は確認されない。マージの前に誰かのチェックが通ったことを示す記録は残らない。
  - 実行するのは push する人の環境だけである。Windows と macOS での実行、Python 3.12 以外での実行は、この仕組みでは確かめられない（今回は Linux、Python 3.13 で実行した）。
  - `versions` は、版を書く箇所の一覧をスクリプトに持つ。版を書く箇所が増えたら、`docs/release.md` の手順 1 と合わせて一覧に足す必要がある。

## 2026-11-20 サーバー側抽出の途中保存（spec v1.14）

- 変更前の確認（コード）：サーバー側抽出は、バッチを `asyncio.gather` で並行に実行し、結果をメモリで統合していた。1 バッチが失敗すると例外がそのまま返り、ほかのバッチの結果は使われなかった。次の `analyze_protocol` は全バッチを送り直していた。サーバーの再起動でも同じだった。
- 変更：検証に通ったバッチをその都度 `extraction_batches`（種別 `provider`）に保存し、次の実行は保存済みのバッチを再利用する。バッチには節の本文の署名を付け、同じ本文のときだけ再利用する。失敗したバッチがあっても、ほかのバッチは最後まで実行して保存する。
- 自動テスト（プロバイダの応答は模擬）：
  - 3 バッチのうち 2 番目が失敗する場合：3 バッチとも実行され、1 番目と 3 番目が保存され、エラーが返る。次の実行は 2 番目だけを問い合わせ、3 バッチ分を統合する（`progress` は `done=3, total=3, reused=2`）。
  - 同じ番号でも本文の違うバッチは再利用しない。今のスキーマに合わない保存済みのバッチは抽出し直す。
  - `Service` を作り直す（サーバーの再起動に当たる）場合：最初のプロセスで 1 バッチ目だけが終わって失敗した後、新しい `Service` の `analyze_protocol` は 1 バッチ目を問い合わせず、残りだけを問い合わせて解析を保存する。保存の後、その研究の途中バッチは 0 件。
- `uv run python scripts/check.py`：全 7 段階が通る（452 passed、1 skipped。追加 3 件、既存の 1 件は `progress` に `reused` が加わった分だけ期待を改めた）。`extract_with_provider` を差し替えるテストの代役 9 か所は、引数 `checkpoint` を受けるようにした。
- 限界：
  - 実プロバイダでは実行していない（API キーを使わない方針の環境で、応答は模擬）。実際の障害（タイムアウト、429、途中で切れた JSON）の後の再開、並行の書き込みの下での所要時間は未確認。
  - 進行中のタスクはメモリにあり、再起動の時点で実行中だったバッチは失われる（保存されるのは終わったバッチだけ）。
  - 同じ DB を使う 2 つのサーバーが同じ研究を同時に抽出すると、同じバッチを両方が問い合わせうる（結果は同じ鍵に上書きされ、壊れはしない）。
  - エラーは全バッチが終わってから返るので、最初の失敗から応答までの時間は、残りのバッチの分だけ延びる。
  - `research_protocol` の全文一括回答のバッチは保存していない。

## 2026-11-20 `research_protocol` の待機（spec v1.15）

- 2026-09-24 の未検証事項（Codex の `tool_timeout_sec=180` に対し、`research_protocol` の一括回答が約 2 分かかり、ポーリング化していない）への対応。プロバイダの探索をサーバーのタスクにし、`LLM_WAIT_SECONDS` を超えたら `status=answering` を返す。`analyze_protocol` の `status=extracting` と同じ形である。
- 自動テスト（プロバイダの応答は模擬）：
  - 回答が待機より長くかかる場合、最初の呼び出しは `status=answering` を返し、その後の呼び出し（`force=true` を含む）は同じ探索に合流する。プロバイダへの問い合わせは 1 回だけ。探索が終わると次の呼び出しが `answered` を受け取り、その次は保存済みの回答（`cached=true`）を返す。
  - `poll=False`（CLI の経路）は `status=answering` を返さずに最後まで待つ。探索が失敗した場合はそのエラーを返し、次の呼び出しは新しい探索を始める。
- `uv run python scripts/check.py`：全 7 段階が通る（454 passed、1 skipped。追加 2 件）。MCP のツールの引数は変えていない。
- 限界：
  - 実プロバイダと実クライアント（Codex、Claude Code）では実行していない。実際のタイムアウトの下で、2 回目の呼び出しが回答を受け取ることは未確認。
  - 動いている探索はプロセスのメモリにあり、サーバーの再起動で失われる。全文の一括回答のバッチは保存していないので、再起動の後は最初からになる。
  - 保存に至らなかった結果（`exploration_limit_reached`）は、終わった後の最初の呼び出しだけが受け取る。その呼び出しがクライアント側で失われると、次の呼び出しは探索をやり直す。

## 2026-11-20 MCP ツールの注釈と進捗通知（spec v1.16）

- 全 18 ツールに `title` と 4 つのヒントを付けた（`mcp/server.py` の `ANNOTATIONS`）。割り当ては各ツールのコードを読んで決めた：解析の置き換えは履歴に残るので破壊的としない（`storage._archive_analysis`）。回答の保存は `INSERT OR REPLACE` で履歴が無いので破壊的とする。`cache_protocol_analysis` は研究ページの確認（`_context`）で通信しうるので外部通信とする。
- 進捗通知：`compare_protocols` は研究ごと、サーバー側抽出の `analyze_protocol` は終わったバッチの数が変わるたびに送る。クライアントが進捗を求めていない呼び出しには送らない（SDK の `report_progress` が `progressToken` の無い呼び出しでは何もしない）。
- 自動テスト：
  - メモリ内で接続した MCP クライアントから `list_tools` を呼び、全ツールに題名と 4 つのヒントがあること、読み取り専用の 7 ツールと破壊的の 4 ツールの集合、進捗の受け口（`ctx`）が入力スキーマに無いことを確かめた。
  - 同じクライアントから進捗を求めて `compare_protocols` を呼ぶと、2 研究で `(0, 2)`、`(1, 2)` の通知が研究 ID 付きで届く。進捗を求めない呼び出しも成功する。
  - サーバー側抽出（模擬）で、終わったバッチの数（3 中 1）が通知される。
  - 実 stdio のテストで、注釈と題名が届き、`ctx` がスキーマに無いことを確かめた。
- `uv run python scripts/check.py`：全 7 段階が通る（458 passed、1 skipped。追加 4 件）。
- 限界：
  - 実クライアント（Codex、Claude Code）が注釈をどう扱うか（確認の省略、表示）と、進捗をどう表示するかは確かめていない。クライアントによっては注釈を使わない。
  - 注釈は手がかりであり、サーバーは強制しない。`search_studies` と `plan_study_search` は、利用者辞書があるときに未一致語の記録を書くが、読み取り専用としている。
  - `research_protocol` の進捗は送らない。進捗でクライアントのタイムアウトが延びるかどうかは、クライアントの実装による。

## 2026-11-20 CLI と MCP の引数の照合（spec v1.17）

- 2026-10 の監査で「コードの判断が要るもの（未対応）」とした、CLI と MCP の引数の対応の欠け（`analyze --detail`、`cache-analysis --batch-offset` など）を調べ直した。CLI の 23 のサブコマンドと MCP の 18 のツールの引数、既定値、選択肢、範囲を機械的に並べて比べた。
- 結果：
  - 引数の名前：18 のツールの引数は、すべて対応するサブコマンドから指定できる。欠けは無かった（監査の後に補われていた）。CLI だけの引数は、非表示で何もしない `search --all-studies` の 1 つ。
  - 既定値と選択肢：すべて一致した。`search` の DARWIN EU の既定も、MCP と同じ「絞らない」である。
  - 範囲：MCP のスキーマにある下限・上限・文字数の上限を、範囲のすぐ外の値 22 通りでコアに渡し、`INVALID_INPUT` で拒否されるかを確かめた（うち 14 通りは `ema-rwe` のコマンドからも実行し、同じエラーになることを見た）。食い違いは 2 種類あった。(1) `compare_protocols`、`research_protocol`、`cache_protocol_answer` の `question` は、コアが 2000 字を超えると拒否するのに、スキーマに上限が無かった。(2) `cache_protocol_analysis` の `question` は、スキーマに上限があるのに、コアはその呼び出しでは拒否せず、最後の保存で初めて拒否していた。両方を揃えた。
- 追加したテスト（`tests/test_cli_mcp_parity.py`、6 件）：全ツールにサブコマンドがあること、引数の集合が一致すること（構造を持つ引数は項目ごと）、必須の引数が位置引数であること、既定値、選択肢、スキーマの範囲をコアが拒否すること。スキーマに範囲を足したのにコアの検査の呼び出しを足さない場合も落ちる。
- テストが食い違いを見つけることの確認：`ask` に CLI だけの引数を足し、`analyze --max-chars` の既定値を変えると、6 件のうち 3 件が落ちた。元に戻して通ることを確かめた。
- `uv run python scripts/check.py`：全 7 段階が通る（464 passed、1 skipped）。
- 限界：
  - 照合するのは引数の形（名前、必須、既定値、選択肢、範囲）である。同じ引数が同じ動作になることは、個々の機能のテストに任せている。
  - 対応の表はテストの中に手で書いている。決まりに無い読み替えを足すときは、表と spec v1.17 の 2 を改める必要がある。
  - `plan_study_search` の `question` には、スキーマにもコアにも文字数の上限が無い（今回は変えていない）。

## 2026-11-20 カタログのスナップショットの取り込み（spec v1.18）

- 計画（`docs/research/202610081527_code_data_versioning_plan.md`）の段階 2 と 3。更新の既定は、利用者の判断で `apply`（起動時に確かめて自動で取り込む）とした。
- 自動テスト（GitHub は `httpx.MockTransport` で模擬。スナップショットはテスト用のエクスポートから `build_snapshot` で実際に作ったもの）12 件：
  - 新しいスナップショットでカタログが置き換わり、利用者の回答が残る。下書きと、ファイルのそろわないリリースは候補にしない。manifest は GitHub と同じく転送（302）を経て取得する。もう一度実行すると `up_to_date` で、要求はリリースの一覧の 1 回だけ。
  - `--check` に当たる呼び出しは `available` を返し、本体を取得せず、DB を変えない。
  - 本体を書き換えると `invalid_snapshot`（チェックサム）で、DB は変わらず、取得したファイルは残らない。
  - `min_app_version` か `schema_version` が合わないと `app_update_required` で、本体を取得しない。
  - 接続できない、要求の上限（403 と `x-ratelimit-remaining: 0`）、HTTP 500 は、例外にならず `unreachable` か `rate_limited` として記録される。
  - 転送先が GitHub の外だと拒否する。
  - スナップショットの作成より後に利用者が取り込んだ CSV があると `kept_local_import`。
  - 手で取得したファイル（`archive` と `manifest`）は、要求なしで取り込める。
  - サーバーの起動時の確認は、利用者が場所を指定した DB では何もしない。対象の DB では 1 回実行し、同じ日の 2 回目は要求しない。`check` は知らせるだけ、`off` は対象外。確認の中の予期しない例外はサーバーを止めない。
- 実際の GitHub での確認（この環境から `api.github.com` と `github.com` に接続できた）：
  - 作業用の DB に、公開済みの `data-20261001` を手動の経路（`--file`、`--manifest`）で取り込んだ：3,314 研究、`catalogue_snapshot=20261001`。
  - `ema-rwe update-catalogue --check`：`available`、`latest_snapshot=20261102`。
  - `ema-rwe update-catalogue`：`updated`。3,343 研究、`catalogue_snapshot=20261102`、種別 3 つ、`status=current`。コマンドの起動から終了まで 6.1 秒（13 MB の取得、検証、差し替えを含む）。
  - もう一度実行：`up_to_date`。
  - wheel を入れた環境で、空の利用者データフォルダ（`XDG_DATA_HOME`）と既定の設定で `ema-rwe-mcp` を stdio で起動した：同梱 DB（20261102）が用意され、2 回目の `catalogue_status` の時点で `update` が `mode=apply`、`automatic=true`、`result=up_to_date`、`latest_snapshot=20261102` になった。`search_studies` は候補を返した。同じ日にもう一度起動すると、`checked_at` は変わらず、確認は行われなかった。
- `uv run python scripts/check.py`：全 7 段階が通る（476 passed、1 skipped）。CLI と MCP の照合は、`update-catalogue` を CLI だけのコマンドとして表に加えるまで落ちた（意図どおり）。
- 限界：
  - サーバーの起動時に実際に取り込む経路（`automatic=true` で古いカタログから新しいスナップショットへ）は、模擬でしか確かめていない。実際の GitHub で確かめたのは、起動時の確認が `up_to_date` になる場合と、CLI での取り込みである（同梱 DB が最新のスナップショットと同じ日付のため）。
  - チェックサムは同じリリースの manifest から取るので、確かめられるのは転送と保存の途中で壊れていないことである。公開者の真正性は、コードと同じ GitHub のリポジトリへの信頼による（署名は付けていない）。
  - GitHub の API は、認証なしでは IP ごとに 1 時間 60 回まで。1 日 1 回の確認なら足りるが、同じ IP の利用者が多い組織では上限に達しうる（その日は `rate_limited` になり、翌日に確かめる）。
  - 起動時の確認は別のスレッドで動くので、起動の直後の検索は古いカタログで行われることがある。差し替えは 1 回の書き込みトランザクションで、検索はどちらかのカタログを見る。
  - Windows と macOS、プロキシの設定がある環境では実行していない。
  - 計画の段階 4（結果へのスナップショットの日付の記録、月ごとの DB のコミットの廃止）と段階 5（アプリの新しい版の通知）は行っていない。

## 2026-11-20 検索条件の引数の整理（動作を変えない変更）

- 変更前の確認（コード）：検索の条件は、`Repository.search`（13 個）、`_match_scope`（14 個）、`_analogous_screen`（11 個）などを位置引数で受け渡していた。`_fallback` は、条件を並べたタプルの 1、4、8、9 番目を取り出して使っていた。MCP と CLI のアダプタは、サービスを最大 14 個の位置引数で呼んでいた。どれも、順序を 1 つ間違えても型が似ているため動いてしまう。
- 変更：
  - 条件を `SearchRequest`（変更できないデータクラス）にまとめ、`Repository.search(query, request, limit, exclude)` と `concept_ids(query, request)` にした。絞り込みの補助関数は `request` を受け取り、条件を変えるときは `dataclasses.replace` で名前を指定する。
  - どこからも呼ばれていなかった `_union` を除いた。
  - MCP と CLI からサービスへの呼び出し 23 か所を、サービスの関数の引数名を使って名前付きに書き換えた（スクリプトで、関数の定義の順から機械的に対応させた）。
  - `search_studies` と `compare_protocols` の任意の引数を、名前でしか渡せないようにした。
- 動作が変わらないことの確認：
  - 同梱のカタログ（3,343 研究）で、検索 11 通りと絞り込み 11 通り（役割、DARWIN EU、状態、類義語、コード、国・種別・デザインの絞り込み、類縁概念、0 件のときの代替案、複数ブロック、ATC のクラス、未知の ATC コード）を、変更前（main）と変更後のコードで実行し、結果の JSON（372 KB）がバイト単位で一致した。通信が起きないよう、プロトコルの取得は差し替えた。
  - `uv run python scripts/check.py`：全 7 段階が通る（477 passed、1 skipped）。`Repository.search` を直接呼ぶテストとスクリプトの 22 か所は、新しい呼び方に書き換えた（検索の内容は同じ）。位置で渡すと `TypeError` になることのテストを 1 件加えた。
- 確認の途中の誤り：最初の比較では、1 つのケース（候補が一次判定の上限以内）が実際に EMA へプロトコルを取りに行った。変更前の実行は 3 研究のうち 2 研究で 429、変更後の実行は 3 研究とも取得できた（合わせて研究ページと PDF を十数回要求した）。結果の差はこの通信の成否だけだったが、意図しない通信だったので、取得を差し替えてやり直した。上の「一致した」は、やり直した結果である。
- 限界：比較したのは 22 通りで、利用者辞書のある構成と、内部 LLM を使う `plan_study_search` は含まない（どちらも今回の変更の経路を通らない）。

## 2026-11-20 結果への版の記録、同梱カタログの更新の仕方、アプリの新しい版の通知（spec v1.19）

- 計画（`docs/research/202610081527_code_data_versioning_plan.md`）の段階 4 と 5。
- 版の記録：解析の `source`、質問別回答と比較の `versions` に、アプリの版、カタログのスナップショットの日付、parser の版を入れた。fingerprint は変えていない（保存済みの解析はそのまま有効）。
- アプリの新しい版：カタログの確認で取得するリリースの一覧から `vX.Y.Z` の最大を記録し、動いている版より新しいときだけ `catalogue_status.app_update_available` を返す。要求は増えない。
- 同梱カタログ：`scripts/build_catalogue_snapshot.py --verify <フォルダ> --unpack-to <ファイル>` を加えた。検証に通った DB だけを、その場所に置く。
- 自動テスト（追加 3 件）：
  - リリースの一覧に動いている版しか無いと通知は無く、`v9.10.0` と `v9.9.9` があると `latest=9.10.0` を返す（数として比べる）。プレリリースと `vX.Y.Z` でないタグは数えない。後の確認が失敗しても、分かっている版は保つ。
  - 比較、解析（バッチの応答と保存した解析）、質問別回答に版が入り、比較表の末尾に 1 行が付く。スナップショットを取り込んだ後は新しい結果がその日付を示し、保存済みの解析は作成時の値のまま。
  - `--unpack-to` は、検証に通った DB を置き（SHA-256 が manifest と一致、ほかのファイルを残さない）、検証に通らないときは元のファイルを変えない。`--verify` なしでは使えない。
- 実際の GitHub での確認：
  - `ema-rwe update-catalogue --check`：`latest_app_version=0.5.6`（公開済みの最新。動いている版は 0.5.7）で、`app_update_available` は無い。
  - 公開済みの `data-20261102` を取得して `--verify --unpack-to` で展開した：`verified=20261102`、3,343 研究。その DB で `catalogue_status` は `status=current`、種別 3 つ、`snapshot_aligned=true`。今の同梱 DB と比べると、研究 3,343 件と補完の記録 455 件が同じで、違うのは取り込みの記録の数（4 件と 15 件。同梱 DB は 3 回分のエクスポートを重ねている）だけだった。
- `uv run python scripts/check.py`：全 7 段階が通る（480 passed、1 skipped。この記録と PR 92 の本文には 481 と書いたが、数え間違いで、実際は 480 だった）。
- 限界：
  - 月ごとの新しい手順（作業用の DB に最新のスナップショットを取り込み、新しいエクスポートを取り込み、その補完の結果でスナップショットを作る）は、通しでは実行していない。この環境には新しいエクスポートが無いため。個々の段階（`update-catalogue`、`import-all`、`--observations-from` を使う `build_snapshot`、`--verify`）は、それぞれテストか実行で確かめている。次の月の公開のときに、手順どおりに進むことを確かめる必要がある。
  - `app_update_available` が現れる場合は、模擬でしか確かめていない（公開済みの最新が動いている版より古いため）。伝え方の文にある `uvx --refresh` は、uv にその引数があることだけを確かめた。
  - 同梱 DB は、この変更では置き換えていない（今の同梱 DB と公開済みの最新のスナップショットは同じ日付）。
  - 版の記録は、今回以降に作った結果に付く。以前の解析と比較には無い。

## 2026-11-20 抽出の正解データによる検査、節の続きのページ（spec v1.20）

- 正解データ：研究 49324（108 ページ、13 項目）、49276（70 ページ、16 項目）、49285（69 ページ、10 項目）、105807（82 ページ、12 項目）の組入れ基準と除外基準、計 51 項目。プロトコルを読んで列挙した。リポジトリに置くのは、説明、ページ、短いパターン、採点で受け入れる語だけで、本文は置かない（テストが、パターンが 45 文字・7 語以内であることを確かめる）。
- サーバーの判定の検査（`extraction_eval.py check --require`、実物の 4 つの PDF）：
  - 最初の実行は 47/51。研究 49324 の除外基準の 4 項目（次のページに続く後半）が、`research_protocol` が正解の質問で返す範囲に入っていなかった。そのページは検索語を含まず、単独では順位が低い。節の続きのページをヒットと一緒に返すようにして（要件 7）、51/51 になった。
  - 続きのページを返さない設定に戻すと、同じ 4 項目で失敗し、終了コード 1 になる（検査が、この退行を捕まえる）。
- push の前：`uv run python scripts/check.py` の `gold` の段階が 4 つのプロトコル、51 項目で通る。全 8 段階で 31 秒（`gold` は 4 秒ほど）。490 passed、1 skipped（追加 10 件：合成の PDF での検査の各判定、続きのページ、プロトコルが無いときと `--require`、採点、正解ファイルの形、スナップショットのビルドでの実行）。
- 月ごとのスナップショット：テスト用のエクスポート（正解の研究を含まない）でビルドすると、終了コード 1 と「公開しない」の表示になり、書き出した DB の SHA-256 は manifest のままで、標準出力は manifest だけ。`--skip-gold` では実行しない。今のカタログ（20261102）で `extraction_eval.py catalogue` は 4 件とも載っている。
- EMA での確認：
  - `extraction_eval.py fetch`：1 件を保存先から外して実行し、取得できた（残りは `present`）。同梱の `data/ema.sqlite3` は変わらない（複製で行う）。
  - `extraction_eval.py catalogue --online`：4 件とも「最新のプロトコルは正解の文書のまま」。間隔を空けない最初の実装では、3 件目から 429 になり、同梱 DB も書き換わった。研究ごとに 30 秒空け、複製で行うように直した。
- 読み手の抽出の採点（`extraction_eval.py grade`）：v0.5.7 の方式の比較などで保存してあった、呼出元抽出の 27 回分（4 つのプロトコル）を採点した。
  - 26 回は下限（回答 90%）を満たす（回答は、プロトコルの全項目か、1 項目だけ欠ける。`cohort` は全項目）。当時から抽出が粗いと分かっていた 1 回（研究 105807、12 項目中 10）だけが下回る。
  - 最初は `pattern` だけで採点し、下限を満たさない回が 8 つあった。保存済みの回答を見ると、読み手は基準に触れているが、別の言い方（「≥18」と「18 years」）や、同じ基準の別の行の引用だった。採点用の語（`accept`）を項目ごとに分けた。
- 限界：
  - 対象は、組入れ基準と除外基準、英語で文字層のある 4 つのプロトコルだけ。データソース、アウトカム、コードリスト、スキャンの PDF、ほかの言語は確かめていない。
  - 採点は LLM を伴うので、push の前には実行しない。内部 LLM での `run` は、この環境に LLM の設定が無く、実行していない（`grade` は、呼出元抽出の保存済みの結果で確かめた）。今回の変更の後の版での、新しい抽出の採点もしていない。
  - `accept` の語は緩い。採点は「その基準に触れているか」で、数値や条件の正しさは見ない。同じ語を持つ 2 項目（研究 105807 の 2 つのコホートの年齢の基準）は、片方の記載で両方が満たされる。
  - 取得していない clone では、pre-push hook が `gold` で止まる（`fetch` を 1 回実行するか、`--no-verify`）。EMA に接続できない環境では、`--allow-missing-gold` で手元の分だけになる。
  - 月ごとの手順の全体は、次のスナップショット（12 月）で初めて通す。

## 2026-11-20 アウトカム定義の正解データ、質問でのプロトコル内検索の改良（spec v1.21）

- 正解データ：`outcomes.json`。同じ 4 つのプロトコルのアウトカムの節を読み、27 項目を列挙した（研究 49324 が 5、49276 が 6、49285 が 4、105807 が 12）。挙げられたアウトカム、同定に使う登録・記録、コード（ICD-10 C25 とその下位、MedDRA は付録にある旨）、感度分析での別定義、SAP に委ねると書かれた点。本文は置かない。
- サーバーの判定の検査（`extraction_eval.py check --require`、実物の 4 つの PDF）：
  - 最初の実行は 10/27。17 項目が、`research_protocol` が正解の質問で返す範囲（40,000 文字）に入っていなかった。研究 49285 と 105807 は、アウトカムの節の全項目が外れた。原因は 3 つ：質問の each、from、which、it が内容の語と同じ重みで数えられ、要旨やデータソースの節が上に来る。outcome が、見出しの Outcomes に一致しない。数行しかないアウトカムの節は、見出しに語があっても点が低い。
  - 文法の語を外し、単純な複数形・単数形を一致とすると、アウトカムは 26/27 になったが、組入れ・除外基準が 47/51 に下がった（研究 49324 の除外基準の後半の 4 項目）。そのページが、語を 1 つ含む下位のヒットになり、続きのページとして扱われなくなっていた。下位のヒットも、上位のヒットの節の続きなら隣に返すように直して 51/51。**v1.20 で入れた正解データが、この退行を push の前に捕まえた。**
  - 続きのページの直しの後、研究 49285 のアウトカムの 4 項目が、再び上限の外に出た（上位の節の続きが増えた分）。見出しでの一致の加点を 2 から 4 にして、27/27。加点 3、4、5 のどれでも、両方の正解が全項目で通る（4 を採った）。
  - 残る 1 項目（研究 49324 の付録のコードの行、96 ページ）は、最初の検索の範囲に入らない（順位の合計で 83,000 文字付近）。`beyond_first_search` として、その判定だけ求めない。
  - 最終：組入れ・除外基準 51/51、アウトカム 27/27。
- 採点（`extraction_eval.py grade --gold .../outcomes.json`）：以前の比較で保存してあった解析の `outcomes` と `disease_definitions` を採点した。研究 49276、49285、105807 は全項目（6/6、4/4、12/12）。研究 49324 は、回によって 5 項目中 0、2、4（v0.5.7 以降の方式の回は 4。付録のコードの行が無い）。下限 60% では、古い方式の 2 回が下回る。
- `uv run python scripts/check.py`：全 8 段階が通る（491 passed、1 skipped。追加 1 件、正解ファイルの形と採点のテストは複数の題材に合わせて改めた）。34 秒。
- 限界：
  - **アウトカムの質問への質問別回答は、採点していない。** 保存済みの結果は、組入れ・除外基準の質問のものだけで、アウトカムの質問で抽出した結果が無い。`accept` の語は、解析のブロックでしか確かめていない。下限（回答 80%）は、実測に基づかない仮の値。
  - 検索の順位の変更は、4 つのプロトコルと 2 つの質問でしか確かめていない。文法の語の一覧は英語だけ。不規則な複数形は扱わない。見出しの加点を上げたので、見出しに語があるだけの短い節が、上に来やすくなる。
  - アウトカムの正解は、アウトカムの節と主要アウトカムのコードの 1 行まで。付録の表（データソースごとの同定の仕方、コードリストの全体）は含まない。研究 105807 は、グループ 3 の「Real-world outcomes」の節だけで、資源利用や費用の指標は含まない。
  - 付録のコードリストは、質問への最初の検索に入らないことがある（上の 1 項目）。呼出元が続けて検索する前提で、サーバーの側は変えていない。
  - 内部 LLM での `run` は、この環境に設定が無く、実行していない。

## 2026-11-20 変更後の版での抽出の採点、節のラベルの空白、空の最終統合の呼び出し（spec v1.22）

- 方法：呼出元抽出（既定の経路）を、呼出元の役の LLM エージェント 8 つで行い、`extraction_eval.py grade` で採点した。コードは spec v1.21 までを含む main。エージェントは、CLI（MCP のツールと同じ引数）だけを使い、正解データもソースコードも読まない。
  - 全抽出の 4 つ（研究ごとに 1 つ）：アウトカムの質問を付けて `analyze_protocol` を最後まで行い（抽出と回答を 1 回で）、その後、組入れ・除外基準の質問を `research_protocol` で回答した。
  - 回答だけの 4 つ：解析の無い DB で、アウトカムの質問を `research_protocol` で回答した（検索の経路）。
- 採点の結果（回答に含まれる項目 / その引用自体に含まれる項目 / 解析のブロックに含まれる項目）：

  | 研究 | 組入れ・除外基準（回答は検索の経路） | アウトカム（1 回で） | アウトカム（検索の経路。解析なし） |
  |---|---|---|---|
  | 49324 | 13 / 13 / 13（13 項目中） | 5 / 5 / 5（5 項目中） | 5 / 5 / − |
  | 49276 | 16 / 16 / 16（16） | 6 / 6 / 6（6） | 6 / 6 / − |
  | 49285 | 10 / 10 / 10（10） | 4 / 4 / 4（4） | 4 / 4 / − |
  | 105807 | 12 / 12 / 12（12） | 12 / 11 / 12（12） | 12 / 10 / − |

  どの回も、全項目が回答と解析にある（下限は、組入れ・除外基準が 90% と 70%、アウトカムが 80% と 60%）。アウトカムの回答の下限は、この実測で確かめた値になった。
- 検索の経路で、最初に返った範囲に答えがあったか（エージェントの報告）：
  - 組入れ・除外基準：4 つとも、最初の範囲だけで答えた。
  - アウトカム：研究 49276 は最初の範囲だけ。研究 49324 は、定義と同定元は最初の範囲にあり、付録のコード（96 ページ）は続けて検索した（正解で `beyond_first_search` とした項目）。研究 49285 は、アウトカムの一覧は最初の範囲にあったが、定義の本体がある曝露の定義の節（24〜26 ページ）は文字数の上限で本文が省かれ、節 ID で読み足した。研究 105807 は、アウトカムの一覧は最初の範囲にあり、データソースの節と付録のコード表を読み足した。
- 読み手のつまずきと対応：
  - **節のラベル**：回答だけの 4 つすべてが、`section` に見出しを空白 1 つで書き、`EVIDENCE_INVALID` で 1〜2 回拒否された（サーバーの見出しは空白が 3 つ）。空白を詰めて比べるように直した（要件 1）。
  - **空の最終統合**：全抽出の 2 つ（研究 49285、49324）が、統合の後に、データソースの重複や `key_notes` の上限を直そうとして、バッチの無い状態で空の最終呼び出しをし、保存済みの解析が空になった。どちらも `--refresh` で読み直して復旧した。空の呼び出しを断るように直した（要件 2）。
  - そのほかの拒否（直していない。メッセージに従って読み手が直せた）：行をまたぐハイフンを含む引用、表の複数の列にまたがる引用、プロトコルの書き方と違うデータソース名。
- `uv run python scripts/check.py`：全 8 段階が通る（493 passed、1 skipped。追加 2 件）。
- 限界：
  - 各条件は 1 回ずつで、読み手は 1 種類の LLM。回ごとのばらつきは見ていない。採点は「触れているか」で、全項目が通ったことは、内容の正しさの保証ではない。
  - 4 つとも満点で、正解データは、今の版と今の読み手の間の差を見分けていない（退行を見つける用途には使える。以前の方式の回は下限を下回った）。
  - 統合での上限（`key_notes` 20、データソース 60）で、後ろの項目が切られる。バッチ間で重複したデータソース名があると、上限に早く達する（研究 49324）。今回は直していない。
  - アウトカムの定義が別の節（曝露の定義）にあるとき、最初の範囲では本文が省かれることがある（研究 49285）。読み手は節 ID で読み足せたが、サーバーの側は変えていない。
  - 要件 1 と 2 は、テストで確かめた。直した後の版で、エージェントによる抽出をやり直してはいない。

## 2026-11-20 統合での重複のまとめと上限の扱い、名前だけの短い節の隣の節（spec v1.23）

- 名前だけの短い節の隣の節：
  - 正解データに、研究 49285 の曝露の定義の節（24、26 ページ）にあるアウトカムの定義の 4 項目を足した。直す前の `check` は、この 4 項目で失敗する（`research_protocol` が返す範囲に入らない。前回の採点で、読み手が節 ID で読み足した箇所）。
  - 直した後：組入れ・除外基準 51/51、アウトカム 31/31。ほかの 3 つのプロトコルの結果は変わらない（研究 49324 の「Outcome variables」は長い節なので、隣は動かない）。
  - 前回の採点で保存した回答を、足した 4 項目で採点し直した：全抽出の回は 8/8、検索の経路の回も 8/8（読み手が読み足したので含まれている。項目は、読み足さなくても範囲に入るかどうかを `check` で見る）。
- 統合：
  - 読み手が保存したバッチのファイル（前回の採点の 4 つの回。読み手が手で重複を除いた後のもの）を、古い統合と新しい統合に掛けた。データソースは、研究 49324 が 56 → 54、研究 105807 が 12 → 10（表記の違いだけの重複がまとまった）。ほかのリストの件数は同じ。研究 49324 の `key_notes`（26 件）は、どちらも 20 件に切られるが、新しい統合は 5 つのバッチから順に採る。
  - 読み手が手で除く前のバッチ（研究 49324 で、上限 60 に達して後ろのデータソースが落ちたときのもの）は、残っていない。その場面そのものは再現していない。テストでは、同じ名前のデータソースと評価が 1 つになること、planned と used で planned が残ること、36 件の `key_notes` が 2 つのバッチから 10 件ずつ残ることを確かめた。
- `uv run python scripts/check.py`：全 8 段階が通る（495 passed、1 skipped。追加 2 件、上限の注記の文を変えた分の期待を 1 件改めた）。
- 限界：
  - 重複の判定は、表記の一致だけ。略称と正式名、言い換えは別の項目のまま残る。
  - 各バッチから順に採るので、上限を超えたときに落ちる項目が、後ろのバッチから前のバッチの後半に移る。落ちること自体は変わらない（`missing_information` に件数を書く）。
  - 隣の節を上げる条件（1,000 文字以下、番号が 3 階層以上）は、4 つのプロトコルで決めた値。番号の無い見出しのプロトコルでは働かない。
  - 直した後の版で、エージェントによる抽出はやり直していない。

## 2026-11-20 採点での値の照合、誤りの検出、難しい項目（spec v1.24）

- 正解データ：既存の項目に `values` を付け、18 項目を足した（組入れ・除外基準 60、アウトカム 40）。`check`（実物の 4 つの PDF）は、100 項目すべてで通る。書いた直後は 6 項目で失敗した：値が正解のページに無いものが 2 つ（行をまたぐハイフンの「non-melanoma」、前のページにあるコード）で、正解の側を直した。質問への最初の検索に入らないものが 4 つ（潜伏期間、感度分析の表の 2 行、妥当性の確認）で、`beyond_first_search` とした。
- 前回の採点（spec v1.22 の節）で保存した 8 つの回を、新しい正解と採点で採点し直した（回答：触れている / 引用にある / 正しい、解析：触れている / 正しい。括弧は項目数）：

  | 研究 | 組入れ・除外基準（回答、解析） | アウトカム・1 回で（回答、解析） | アウトカム・検索の経路（回答） |
  |---|---|---|---|
  | 49324 | 17/17/17、17/17（17） | 9/8/7、9/8（9） | 9/9/9 |
  | 49276 | 16/16/16、16/16（16） | 7/7/7、7/7（8） | 8/7/8 |
  | 49285 | 15/15/15、15/15（15） | 8/8/8、8/8（8） | 8/7/8 |
  | 105807 | 12/12/12、12/12（12） | 14/11/11、15/14（15） | 15/13/15 |

  - 満点でなくなり、差が見える。**抽出と同時に答えた回（1 回で）は、付録のコードと別の章の値を落とす**：研究 105807 は付録の表のコード 3 項目（1 つは無く、2 つはコードを述べていない）と生存率の年数、研究 49324 は潜伏期間の値と陰性対照のコード、研究 49276 は MedDRA の用語。同じ質問を検索の経路で答えた回は、全項目が正しい（読み手が付録まで検索して読んでいる）。
  - 組入れ・除外基準は、足した 9 項目（付録と変数の定義のコード・閾値）を含めて全項目が正しい。種類の取り違えは、どの回にも無い。
  - どの回も下限は満たす（アウトカムの 1 回での回答は、研究 105807 が 15 項目中 11 で 73%、下限 70%）。
  - 正解に無い事実：組入れ・除外基準の回答で 2〜5 件。研究 49324 と 49276 の分を読むと、誤りではなく、正解に入れていない事実だった（感度分析での条件、副次目的だけの条件、「診断の登録は要らない」という記載）。
- `uv run python scripts/check.py`：全 8 段階が通る（497 passed、1 skipped。追加 2 件）。
- 限界：
  - 値の照合は、数とコードのある 40 項目だけ。値の無い項目は、これまでどおり「触れているか」。
  - 値は、項目に一致した事実のどれかにあればよい。1 つの項目に 2 つの値（24 か月と 60 日）があるとき、別々の事実にあっても正しいとする。
  - 読み手の値が数字を含まなければ引用で足りるので、「成人」と書いて正しい引用を付けた事実は正しいとする。値を言い換えた誤り（「2 年」と「24 か月」は一致しない）は、誤って正しくないとすることがある。
  - 取り違えの検出は、解析のブロックが種類ごとに分かれている組入れ・除外基準だけ。回答の中の取り違えは見ない。
  - 正解に無い事実は、数えるだけで、正誤は人が見る。
  - 採点し直したのは、保存済みの回（spec v1.21 の版での抽出）。spec v1.22、v1.23 の修正の後の版では、抽出をやり直していない。

## 2026-11-20 質問別回答の上限と、バッチごとの回答の統合（spec v1.25）

- 原因の確認：spec v1.24 の採点で、抽出と同時の回答が付録のコードを落としていた。保存済みの 4 つの回の回答は、どれも 30 件ちょうどで、`missing_information` に「57（34、49、40）件が見つかり、最初の 30 件だけを残した」とあった。読み手は付録のコードを見つけていて、統合で落ちていた。
- 読み手が保存したバッチごとの回答を、古い統合（最初の 30 件）と新しい統合に掛け、アウトカムの正解で採点した（触れている / 正しい。括弧は項目数）：

  | 研究 | バッチごとの回答の件数 | 古い統合 | 新しい統合 |
  |---|---|---|---|
  | 49324 | 5、10、7、6、29 | 30 件、9 / 7（9） | 57 件、9 / 8 |
  | 49276 | 4、18、8、4 | 30 件、7 / 7（8） | 34 件、8 / 8 |
  | 49285 | 11、15、20、3 | 30 件、8 / 8（8） | 48 件、8 / 8 |
  | 105807 | 1、25、9、5 | 30 件、14 / 11（15） | 40 件、14 / 13 |

  - 落ちていた付録のコード（研究 49324 の陰性対照のコード、研究 49276 の MedDRA の用語、研究 105807 の付録の表の 2 項目）が戻る。4 つとも 60 件以内で、全件が残る。
  - 残る 3 項目は、統合ではなく読み手の書き方による：研究 49324 の潜伏期間（期間の値を書いていない）、研究 105807 の COPD のコード（回答に無い）と生存率の年数。生存率の年数は、正解の値のパターンが「1-year」の形しか受け付けていなかったので、「1, 2 and 3 years」も受け付けるように正解を直した。
- 41 件以上の回答の保存：これまでの検証は、21 件目以降をまとめて 1 回で検証していて、41 件以上では失敗する作りだった。20 件ずつに直し、45 件の回答が保存され、45 件目の誤った引用が拒否されることをテストで確かめた。
- `uv run python scripts/check.py`：全 8 段階が通る（499 passed、1 skipped。追加 2 件）。実 stdio の MCP のテストを含む（ツールの引数の `answer` のスキーマの上限が変わる）。
- 限界：
  - 新しい統合の効果は、保存済みのバッチごとの回答に掛けて確かめた。直した後の版で、エージェントによる抽出はやり直していない。
  - 60 件を超えると、落ちる回答は残る（各バッチの後半）。4 つの研究では最大 57 件だった。
  - 回答が増えるので、比較表や応答が長くなる。比較の出力での見え方は、確かめていない。
  - 同じ内容を別の言い方で書いた回答（要旨と方法の章の両方から）は、まとまらない。

## 2026-11-20 PR #81〜#98 のレビューで見つかった問題の修正（spec v1.26）

- マージ済みの PR #81〜#98 を main（881b45b）でレビューし、見つかった問題を 1 本の変更で直した。直した挙動は spec v1.26 に書いた。問題ごとに、直す前に落ちる再現テストを先に書いてから直した（下の対応表）。
- 再現テスト（直す前の実行）：
  - `uv run pytest -q tests/test_provider_extraction.py -k "budget or ends_the_protocol or without_their_question or leaves_no_answer or force_refresh_asks or only_notes"`：6 failed、1 passed。`ends_the_protocol` はテスト側の誤りで落ちていたので、直してから単独で実行し、直す前のコードで通ることを確かめた（既存の挙動を確かめるテスト）。
  - `uv run pytest -q tests/test_parse_cache.py -k "matched_by_its_words or nearest_to_any or longer_than or eight_quotes or outside_the_server"`：6 failed（名前の照合の陰性 2 件、近いページ、1,000 字の上限、8 件のメッセージ、`server` の印）、11 passed。
  - `uv run pytest -q tests/test_exploration.py -k "forced or slow_pdf or provider_server_marks"`：4 failed。遅い PDF の読み取りのテストは、直す前は 1.0 秒止まり、直した後は 0.5 秒未満。
  - 短い節の隣の 2 件は直した後に書いたので、HEAD の `src` を作業フォルダに展開して実行した：2 failed。
  - `uv run pytest -q tests/test_catalogue_update.py -k "hand_given or size_the or off_reports or stopping"` と `-k "stopping or leaves_github or imported_after"`：計 6 failed（GitHub の外への `/manifest` の要求、本体の取得、終了の待ちを含む）。`uv run pytest -q tests/test_annotations_progress.py`：2 failed（破壊的なツールの集合、`ConnectionResetError`）。
  - CLI と MCP の照合テストは、`search_studies.limit` の上限の行を消した一時的な複製で、新しい比べ方が落ちることを確かめた（複製は消した）。
  - `uv run pytest -q tests/test_extraction_eval.py -k "budget or closes or whole_number or pre_push"`：4 failed（予算の設定 1000 が無視された、`asyncio.run` に int、`13 patients` が `3` を満たした、削除だけの push で `uv` が呼ばれた）。
  - 第 2 回（組をまたいで残った分）：`callers_server_marks`、`new_nested_block`（`origins` の KeyError）、`raises_is_timed`（計測の行が 0 行）、`reports_each_study`（`(2, 2)` が無い）、`fails_keeps`（warning が無い）、`left_behind`（古い作業フォルダが残った）の 6 件がそれぞれ落ちた。`missing_folder`（#92 の `--unpack-to`）は直す前から通る。
- 直した後：
  - `uv run ruff format src tests scripts`：66 files left unchanged。`uv run ruff check src tests scripts`：All checks passed。
  - `uv run pytest -q tests/test_parse_cache.py tests/test_provider_extraction.py tests/test_extraction_eval.py tests/test_annotations_progress.py tests/test_distribution.py tests/test_snapshot.py tests/test_catalogue_update.py tests/test_exploration.py tests/test_comparison.py tests/test_mcp.py tests/test_cli_mcp_parity.py`：194 passed、1 skipped（実 stdio の MCP のテストを含む）。
  - 組ごとの実行：`tests/test_core.py` ほか 9 ファイルで 211 passed、1 skipped。`tests/test_bookmarks_cohort.py`、`test_extraction_audit.py`、`test_structure.py`、`test_ranking.py`、`test_screening.py` で 127 passed。
  - `uv run scripts/check.py --help` が動く（hook と `gold` の段階の起動の形を変えたため）。`check.py` の `run("no-such-tool-xyz")` と wheel の無いフォルダの `wheel()` は、`SystemExit` で終わる（手で確かめた）。
  - 削除だけの push の hook は、`exec` の行を `echo RUN` に置き換えた写しで、削除だけの入力で RUN が出ず、通常の push と空の入力で RUN が出ることを確かめた。
  - `ema-rwe update-catalogue --file x.gz`（manifest なし）は `INVALID_INPUT` を返す（CLI の名前付きの引数の対応）。
  - `uv run scripts/extraction_eval.py check`（手元に PDF があるのは研究 105807 だけ）：組入れ・除外 12/12、アウトカム 15/15。値の照合に右の数の境界を一律に付けた版では、O14 の `GFEA` がページの `GFEA001` に当たらず 14/15 になったので、コードは下位コードにも当たる規則にした。
  - `uv run scripts/extraction_eval.py fetch` で研究 49324・49276・49285 の PDF を取得した後、`uv run scripts/check.py` の全段階（versions、lock、lint、format、tests、gold、build、wheel）が通った。tests は 534 passed、1 skipped。gold は 4 つのプロトコルすべてで、組入れ・除外 17/17、16/16、15/15、12/12、アウトカム 9/9、8/8、8/8、15/15。
- 訂正：2026-11-19 の `service.py` の分割の節の「`Service` は入口として残し（100 行）」は、分割の時点で 45 行だった。
- 2026-11-20 の spec v1.25 の節の、研究 105807 の「新しい統合 14/13」は、O2（生存率の年数）の正解を直す前と後のどちらで数えたかが分からない。保存済みの回答が手元に無く、再計算していない。今回、O2 の値をさらに厳しくし（`\b1\b\D{0,12}\b2\b\D{0,12}\b3\b`）、値の照合全般に数の境界を付けたので、今の正解と照合規則で `grade` を数え直すと、数値は変わりうる。
- 限界：
  - 実 GitHub（転送の 1 回ずつのたどり、manifest の `created_at` での判断）、実 LLM（`force_refresh` の後の再抽出、探索の `force` の扱い、統合の由来）、実 MCP クライアント（`cache_heading_translations` の破壊的の表示、進捗の最後の通知、通知の失敗）は使っていない。すべて模擬の応答で確かめた。
  - 終了時に止められた更新の作業フォルダは、6 時間より古くなってから始まった更新が消す。それより早く次の更新（手動の `update-catalogue` など）を始めても、その回は消さない。
  - 直した後の版で、エージェントによる抽出と採点はやり直していない。

対応表（直した問題）：

| 元 PR | PR の内容 | 直さなければいけなかった問題 | 直し方 | 主な変更ファイル |
|---|---|---|---|---|
| #81 | 抽出と質問回答を 1 回で行い、途中バッチを DB に保存し、読み残しを検出する（spec v1.11） | 同じ offset を別の `max_chars` で取り直すと、配信の記録が上書きされ、読んでいない節があっても統合が通った（逆に、読んだ節を読み残しと報告した） | バッチを保存するとき、その時点の配信範囲を kind `read` の行として残し、統合のたどりはそれを使う | src/ema_rwe/extraction.py |
| #81 | 同上 | 最終呼び出しで `question` を省く（または文字列が違う）と、バッチの回答が黙って消えた | 別の質問の回答が残っていれば、何も保存せず `INVALID_INPUT` で断る（`Repository.batch_kinds` を追加） | src/ema_rwe/extraction.py、src/ema_rwe/storage.py |
| #81 | 同上 | 回答を保存した後に解析の保存が断られると、古い読み方の回答だけが残った | 解析を保存してから回答を保存する | src/ema_rwe/extraction.py |
| #81 | 同上 | 上の 2 つの経路と、「offset なしの最終呼び出しは末尾まで届く 1 バッチの代わりだけ」の否定側にテストが無かった | 再現テスト 3 件と、中間のバッチを飛ばすと `INCOMPLETE_EXTRACTION` になるテストを足した | tests/test_provider_extraction.py |
| #82 | service.py を 5 つのモジュールへ分ける | README_DEV が `_check_heading_language` を `service` の下に書いていた。検証記録の「`Service` は 100 行」は実際は 45 行 | README_DEV を `extraction.py` に直した。検証記録の数は、この節で訂正した | README_DEV.md、docs/validation.md |
| #83 | 解析済み PDF の再利用、空白の圧縮、データソース名の語の照合、ツールの計測（spec v1.12） | 名前の照合が `;` や文末、名前自身の語をまたぎ、無関係な語の並びを名前として通した | `_span` に本文を渡し、`;`、文末、名前の語をまたぐつながりを切る | src/ema_rwe/pdf.py |
| #83 | 同上 | 引用 8 件の事実では名前の引用を足さないのに、エラーが「本文にも無い」と述べた | 本文に名前があるときは、8 件では足さないので 1 件減らすか名前の箇所を引用せよ、と述べる | src/ema_rwe/pdf.py |
| #83 | 同上 | 名前の周りの切り出しが 1,000 字を超えると、`RWEError` でない `ValidationError` で終わった | 上限を `domain.QUOTE_LIMIT` にし、超える切り出しは足さない | src/ema_rwe/domain.py、src/ema_rwe/pdf.py |
| #83 | 同上 | 例外で終わったツール呼び出しは、計測の行が出なかった | `timed` を try/except/finally にし、例外の型名と `chars=0` で記録してから投げ直す | src/ema_rwe/mcp/server.py |
| #83 | 同上 | 名前を探すページの近さを、最初に引用したページからしか測っていなかった | 引用したどのページからの距離も測り、最小を使う | src/ema_rwe/pdf.py |
| #84 | 別のページにある引用のページの訂正、`evidence[].server` の印（spec v1.13） | 呼出元と内部 LLM が書いた `server` の印を、そのまま保存して配信した | `pdf.without_server_marks` を足し、両方の経路でサーバーの処理の前に消す | src/ema_rwe/pdf.py、src/ema_rwe/exploration.py、src/ema_rwe/extraction.py |
| #84 | 同上 | 手順書が、等距離の 2 ページにある引用も拒否されることを書いていなかった | 手順書に書いた | docs/mcp-workflow.md |
| #85 | `scripts/check.py` と pre-push hook | `while read` が EOF で変数を空にするので、削除だけの push でも検査が走った | ループ内でフラグを立てて判定する | .githooks/pre-push |
| #85 | 同上 | `docs/exploration.md` の `uv sync --extra dev` は、`dev` が依存グループに移った後は失敗する | `uv sync --extra llm` にした | docs/exploration.md |
| #85 | 同上 | `.claude/rules/package-management.md` に反して `uv run python scripts/...` と書いていた | scripts、hook、正解データの README、AGENTS.md、README_DEV.md、docs/release.md を `uv run scripts/...` に揃えた | scripts/、.githooks/pre-push、AGENTS.md、README_DEV.md、docs/release.md |
| #85 | 同上 | `uv` が無いとき、wheel が 0 件のとき、`check.py` が traceback で終わった | `OSError` と wheel 無しを `SystemExit` にした | scripts/check.py |
| #86 | サーバー側抽出の終わったバッチの保存と再開（spec v1.14） | `force_refresh=true` が途中バッチを消さず、仕上げで失敗した抽出が同じバッチで同じエラーを繰り返した | `force_refresh` かつ `offset=0` のとき `clear_batches` を呼ぶ | src/ema_rwe/extraction.py |
| #86 | 同上 | 同期の SQLite 書き込み `checkpoint.save` をイベントループ上で呼んでいた | `asyncio.to_thread` で呼ぶ | src/ema_rwe/llm.py |
| #87 | `research_protocol` を待たずに `status=answering` を返す（spec v1.15） | `status=answering` の後に `force=true` で呼び直すと、終わった探索の結果を捨てて新しい探索を始めた | 受け取られていない結果があれば、`force` の有無に関係なく最初の呼び出しに返す | src/ema_rwe/exploration.py |
| #87 | 同上 | `force=true` の探索中に `force` なしで呼ぶと、古い保存済みの回答を返した | 保存済みの回答より先に動いている探索を調べ、合流する | src/ema_rwe/exploration.py |
| #87 | 同上 | 背景の探索が PDF の解析・検索・保存を同期で行い、イベントループを止めた | `asyncio.to_thread` に移した | src/ema_rwe/exploration.py |
| #88 | 全ツールの注釈と進捗通知（spec v1.16） | `cache_heading_translations` は英訳を写しなしで置き換え途中バッチを消すのに、破壊的でなかった | `destructiveHint=true` にし、手順書を合わせた | src/ema_rwe/mcp/server.py、docs/mcp-workflow.md |
| #88 | 同上 | `compare_protocols` が最後の進捗 `(n, n)` を送らなかった | 取得ループの後に 1 回送る | src/ema_rwe/comparison.py |
| #88 | 同上 | 進捗通知の送信の失敗がツール本体に伝わり、比較の準備が止まった | 送信を try で囲み、stderr に warning を記録して続ける | src/ema_rwe/mcp/server.py |
| #89 | CLI と MCP の引数の照合テスト（spec v1.17） | 範囲の照合が引数単位で、上限を足してもコアの確認なしに通った。既定値の比較が `False`・`0`・`None` を同一視した。`minLength` などを見ていなかった。docstring が入れ子の範囲まで守ると言い過ぎていた | 範囲の種類ごとに比べ、既定値は型と値で比べ、範囲のキーワードを 8 つに広げ、docstring をトップレベルの引数に限った | tests/test_cli_mcp_parity.py |
| #90 | カタログのスナップショットの取り込み（spec v1.18） | 更新中にクライアントが閉じても、取得と置き換えが終わるまでプロセスが残った | 更新をデーモンスレッドで動かし、終了時に待たない | src/ema_rwe/mcp/server.py |
| #90 | 同上（上の修正で生じた問題） | 止まった更新の作業フォルダが DB のフォルダに残る | 作業フォルダに `ema-rwe-update-` を付け、6 時間より古いものを次の更新の開始時に消す | src/ema_rwe/catalogue_update.py |
| #90 | 同上 | 手順書が、自動で取り込まない構成（`EMA_DB_PATH`、checkout、`check`）でも「何もしなくてよい」と書いていた | `automatic=false` か `result=available` なら `ema-rwe update-catalogue` を伝える、と書き分けた | docs/mcp-workflow.md |
| #90 | 同上 | 利用者の CSV の方が新しくても、本体（約 13 MB）を毎回取得してから捨てた | 取得の前に manifest の `created_at` と比べる | src/ema_rwe/catalogue_update.py |
| #90 | 同上 | 転送をたどった後に転送先を確かめるので、GitHub の外にも要求が届いた | 1 回ずつたどり、要求の前に確かめる（5 回まで） | src/ema_rwe/catalogue_update.py |
| #90 | 同上 | 手で渡す manifest の `snapshot_id` の形式も DB の中身との一致も確かめず、`"None"` が状態に残った | `YYYYMMDD` でなければ、また展開した DB の日付と違えば `invalid_snapshot` | src/ema_rwe/catalogue_update.py |
| #90 | 同上 | `refresh_from_bundle` が例外を握りつぶし、理由が残らなかった | stderr に warning（`exc_info`）を記録してから False を返す | src/ema_rwe/storage.py |
| #90 | 同上 | 展開後のサイズに上限が無かった | manifest の `database.size`（上限 4 GiB）を超えたら止める | src/ema_rwe/snapshot.py |
| #91 | 検索条件を `SearchRequest` にまとめる | CLI の `update_catalogue` 呼び出しだけが位置引数だった | 名前付きにした | src/ema_rwe/cli.py |
| #92 | 結果への版の記録、新しい版の通知、同梱カタログの更新方法（spec v1.19） | `EMA_CATALOGUE_UPDATE=off` でも、残った状態ファイルや手動の更新から `app_update_available` が出た | `app_update()` の冒頭で off なら None を返す | src/ema_rwe/catalogue_update.py |
| #92 | 同上 | `--unpack-to` が置き換え前に `-wal`／`-shm` を消すのに、手順書に注意が無かった | DB を開いているサーバーを先に止める、と書いた | docs/release.md |
| #93 | 抽出の正解データによる検査（spec v1.20） | 検査が `research_protocol` の件数 30 と予算 40,000 を写し、予算の数え方も違い、空白を詰める前の本文を使っていた | `exploration.RESEARCH_LIMIT`・`within_budget`・設定の予算・`compact_spaces` 後の本文を使う | src/ema_rwe/exploration.py、scripts/extraction_eval.py |
| #93 | 同上 | `grade()` が開いた `Service` を閉じなかった | async にして `finally` で閉じる | scripts/extraction_eval.py |
| #95 | 節のラベルの空白、空の最終統合の呼び出し（spec v1.22） | 「空の解析」の判定が厳密一致で、`missing_information` だけの繰り返しの最終呼び出しが保存済みの解析を置き換えた | 渡された側も `missing_information` を除いて比べる | src/ema_rwe/extraction.py |
| #96 | 統合での重複のまとめと上限、短い節の隣（spec v1.23） | 「短い節」の長さを一致したページだけで数え、同じ見出しの別の節の長さを合算した | 全チャンクで、同じ見出しが続く連続したチャンクを 1 つの節として数える | src/ema_rwe/pdf.py |
| #96 | 同上 | 親の節とアンカーの下位の節も持ち上がった | 隣の節を親の下の兄弟とその下位に限る | src/ema_rwe/pdf.py |
| #96 | 同上 | 背景などの役割の隣の節も持ち上がった | 隣の節にも `NON_METHOD_ROLES` の除外をかける | src/ema_rwe/pdf.py |
| #96 | 同上 | 初めて受け取った入れ子のブロックで、2 段目のリストの由来が記録されなかった | 空のブロックを置いてから再帰的に統合する | src/ema_rwe/llm.py |
| #97 | 採点での値の照合、取り違えの検出、難しい項目（spec v1.24） | 正解データの README の「範囲」が v1.24 と食い違い、表の括弧が崩れていた | 範囲と `values` の照合規則を書き直した | tests/fixtures/extraction_gold/README.md |
| #97 | 同上 | テストが正解データの件数を直書きしていた | 構造条件（各プロトコルに 1 項目以上、ID が一意）にした | tests/test_extraction_eval.py |
| #98 | 質問別回答の上限 60、バッチごとの回答の統合（spec v1.25） | 研究 105807 の O2 の値 `1\D{0,12}2\D{0,12}3` が `12 months and 3 years` にも一致した。値の照合全般が数の一部（`13` の `3`）に一致した | O2 に数の境界を付け、値の照合を `states()` にまとめた（数は前後に数字が続かない、コードは下位コードにも当たる） | tests/fixtures/extraction_gold/outcomes.json、scripts/extraction_eval.py |
| #98 | 同上 | 検証記録の 105807「14/13」が、O2 の正解を直す前と後のどちらで数えたか分からない | 分からないこと、今回 O2 をさらに厳しくしたこと、`grade` を再計算していないことを、この節に書いた | docs/validation.md |
| #81〜#98 | 文書の反映 | README_DEV がスナップショットの取り込みと逆のことを書き、アプリの更新の案内が版を固定しない設定で働かず、範囲表記・統合の規則・探索の待ち・データソース名の規則・GitHub への通信の記載が古いか無かった | 下の「文書の反映」に挙げた箇所を直した | README.md、README_DEV.md、AGENTS.md、docs/ |

文書の反映（PR #81〜#98 による変化のうち、文書に無いか古かったもの）：

- README.md：版を固定しない設定でのアプリの更新（`uvx --refresh`）、`EMA_DB_PATH`・checkout・`off` では新しい版の通知が出ないこと、起動時の GitHub への通信（データの出所とプライバシー）。
- README_DEV.md：仕様の範囲（v1.26）、スナップショットの取り込みを「まだ取得しない」としていた箇所、既定 DB の 2 つの更新経路、統合の規則（v1.23、v1.25）、探索のタスクと `force`（v1.15）、データソース名の規則とページの訂正（v1.12、v1.13）、計測の行、`server` の印、注釈と進捗、空の最終呼び出し、正解データによる採点、スクリプトの起動の形。
- AGENTS.md：仕様の範囲（v1.26）、スクリプトの起動の形。
- docs/mcp-workflow.md：注釈と進捗、`catalogue_status` の `update.automatic=false` のときの案内、`question` の要る最終呼び出し、`force_refresh`、`INCOMPLETE_EXTRACTION` の 2 つの意味、データソース名の規則、等距離の引用、`server` の印、上限を超えたリストの注記、`research_protocol` の `force`。
- docs/exploration.md：範囲の表記、`uv sync --extra llm`、検索の照合の規則と `continues`・`beside`（v1.21、v1.23）、探索の `force`。
- docs/comparisons.md：比較表の末尾の版の行（v1.19）。
- docs/release.md：スクリプトの起動の形、`--skip-gold`、`misplaced` での失敗、`--unpack-to` の前にサーバーを止めること。
- CHANGELOG.md：Unreleased にこの修正を追記した。

直さなかった指摘：

| 元 PR | 指摘 | 直さなかった理由 |
|---|---|---|
| #84 | ページの訂正は最も近いページを選ぶので、繰り返す定型文は別のページに移りうる | PR の限界の節に書かれている |
| #85 | hook は `--allow-missing-gold` なしで検査するので、正解のプロトコルを取得していない clone では push が `gold` で止まる | 後続の spec v1.20 の設計どおりで、README_DEV と検証記録に書かれている |
| #86 | 呼出元の経路とプロバイダの経路を交互に使うと、互いの途中バッチを消し合う | どちらの経路を通るかは内部 LLM の設定で決まる。交互になるのは設定を変えたときだけで、そのときは fingerprint も変わるので、消すのが正しい |
| #86 | 実プロバイダでの再開（タイムアウト、429）が未確認 | 検証記録に書かれている |
| #87 | 回収されない完了タスクが `_running` に残る | spec v1.15 の 3 は、保存に至らなかった結果とエラーを終わった後の最初の呼び出しに渡すことを求めるので、回収まで残す必要がある。残るのは同じ質問で二度と呼ばれないときのメモリだけで、プロセスの寿命の範囲に収まる |
| #88 | `search_studies`／`plan_study_search` は未一致語の記録を書くのに読み取り専用 | PR で明示した判断で、spec v1.16 の 3 と一致する |
| #89 | `cache_protocol_analysis` は空の `question`（answer なし）を拒否しない | PR の範囲外の小さな不一致で、害は無い |
| #91 | アダプタの引数名の対応を直接確かめるテストが無い | 捕まえるには 18 ツールの入力値とサービス引数名の表が要り、その表は server.py の呼び出しを写すだけになる。レビューの AST の bind 検査で現状の全件整合を確かめた |
| #91 | `SearchRequest` は frozen だが list を持つので浅い不変にとどまる | request の list を変える箇所は無い |
| #92 | `--unpack-to` の親フォルダが無いと traceback になる | 現在のコードでは起きない（`OSError` として捕まえ、`SNAPSHOT_INVALID` の JSON を出して終了コード 1）。回帰のテスト `test_unpacking_into_a_missing_folder_is_refused_as_json` を足した |
| #92 | `versions()` が呼ぶたびに DB を開く | 件数が小さく、影響は小さい |
| #92 | `app_update_available` の実際の出現は模擬だけで、月ごとの手順も通しでは実行していない | PR と検証記録に書かれている |
| #94 | `mentions` の複数形の規則は語によって無意味な形を作り、`-es`／`-ies` に一致しない | 語の境界で照合するので誤一致は無く、spec v1.21 の「単純な」の範囲 |
| #94 | `QUESTION_WORDS` に `not`／`no`／`all`／`any` を含む | spec v1.21 の 5 どおり |
| #98 | `source_assessments` は先頭から 120 件のまま | spec v1.25 の 4 どおり |
| 全体 | ホスト型の CI が無く、`gold` は pre-push でだけ走る | 設計どおりで、文書に書かれている |
| 全体 | 配布サンプルと README が未作成のタグ `v0.5.7` を固定している | docs/release.md の手順（版を揃える PR、main で検査、タグ）の途中の状態なので、タグを作るまでこのまま |
