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
- 詳細は[`data-source-linkage-20260912.md`](data-source-linkage-20260912.md)。原本は`data/imports/data-sources`、Studies原本は`data/imports/studies`へ分離し、どちらもGit管理しない。

## 未検証事項（継続）

- 外部LLM API呼出しの実認証検証は未実施。キーなしの呼出元LLM方式は合成PDFで保存・再利用まで検証。
- 仕様の10〜20プロトコルの人手精度評価、研究種別を跨ぐ5件の実PDF E2E評価は未実施。
- セクションの検出・関連箇所の選択はヒューリスティック。表の複雑なレイアウトや画像の定義は欠落する可能性がある。全文の恒久索引・OCR・意味的rerankingは含まない。
- 引用の照合は根拠の存在を検証するが、LLMの解釈・データソース使用状態・コードの意味を保証しない。
- 版番号が欠落／不正確な場合は `selection_reason` の不確実性を確認する。
- 複数プロセスで共有できるSQLiteを使うが、HTTPの2秒制限は各サーバープロセス単位。

## 人手評価の進め方

将来の検証対象として、記述研究、new-user cohort、active-comparator、SCCS、DUS、非DARWIN研究を含む10〜20件を選ぶ。各フィールドをCorrect / Partially Correct / Incorrect / Not Extracted、引用をCorrect / Incorrectで評価する。特にデータソース全件の再現、候補と使用予定の識別、疾患定義のコードと観察期間、PDF物理ページと印字番号の違いを記録する。
