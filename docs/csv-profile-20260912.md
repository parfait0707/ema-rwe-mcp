# EMA Studies CSV実データ調査（2026-09-12）

> 2026-09-12時点の調査記録。その後、Data source typeは通常、種別で絞ったexport（`data/imports/source_type/`）から付けるようになった（`get_study`で詳細ページを取り直した研究だけは、詳細ページのF8.7の値に置き換わる）（現行の手順は[README_DEV.md](../README_DEV.md)）。

対象は`data/imports/20260912_export-data.csv`。原本には連絡先欄があるためGit管理せず、以下は集計値だけを記録する。

- SHA256: `2332a0375564e43eee7d3fe4eec2c8a50a2bc7bf1e1d730fa81fdb2db4354b3e`
- UTF-8 BOM付き、カンマ区切り、116列、3,312行
- 全行が116列で、Study IDは3,312件すべて数値・一意
- 全行のStudy typeは明示的な`Non-interventional study`
- CSV値にはRFC 4180形式の二重引用符エスケープが含まれる。`csv.Sniffer`が`doublequote=false`と誤判定したため、区切り文字だけを推定し、引用規則を固定する必要があった。

## MCP必須項目

MCPの取込必須項目はStudy ID、Title、Study typeである。3項目とも全3,312件で欠損0件（0.00%）。Study IDの形式不正、空欄、重複も0件だった。`URL LINK`、`Study countries`、`Study status`、`EU PAS number`、`DARWIN EU® study`も欠損0件だった。

## 調査・検索に必要な主な項目

分母は全3,312件。条件付き項目の空欄は必ずしも品質不良ではない。たとえば医薬品項目は医薬品を対象にしない研究では不要で、`Non-interventional study design, other`は`Other`を選ばない研究では不要である。

| 項目 | 欠損件数 | 欠損率 | 解釈 |
|---|---:|---:|---|
| Study description | 326 | 9.84% | 候補検索の説明文が弱くなる |
| Non-interventional study design | 182 | 5.50% | 定型design filterに影響 |
| Study design | 2,565 | 77.45% | 自由記述欄。定型欄と併用する |
| 3つのdesign欄すべて | 150 | 4.53% | design情報をCSVから得られない |
| Data collection methods | 1,104 | 33.33% | secondary use等の判定に影響 |
| Protocol file URIまたはProtocol URL | 1,310 | 39.55% | CSV上でプロトコル所在を確認できない |
| Data source(s) | 2,455 | 74.12% | カタログに紐づくsource名 |
| Other linked data sources | 2,441 | 73.70% | 自由記述のsource名 |
| 上記2つのsource欄の両方 | 1,945 | 58.73% | CSVからsource名を得られない |
| Data source type | 3,312 | 100.00% | Studies exportに列自体がない |
| Medicinal condition to be studied | 736 | 22.22% | 疾患候補検索に影響 |
| 2つのcondition欄の両方 | 660 | 19.93% | CSVから疾患名を得られない |
| Medicinal product name | 2,283 | 68.93% | 条件付き項目 |
| INN/common name | 1,969 | 59.45% | 条件付き項目 |
| ATC code | 1,773 | 53.53% | 条件付き項目 |
| 4つの医薬品識別欄すべて | 714 | 21.56% | 医薬品に無関係な研究を含む |
| Outcomes | 622 | 18.78% | outcome候補検索に影響 |
| Data analysis plan | 270 | 8.15% | 解析法候補検索に影響 |
| Short description of study population | 1,148 | 34.66% | 集団候補検索に影響 |
| Comparators | 3,028 | 91.43% | 条件付き項目 |

`Protocol file(s)`とそのURIは2,001件にあり、Protocol URLだけを含めると所在を持つ研究は2,002件だった。ただしCSV上の所在は最新版選択の根拠には使わず、選択した研究のStudy documentsで全候補を比較する。

`Data source(s)`は857件、`Other linked data sources`は871件にあり、少なくとも一方を持つ研究は1,367件だった。前者はカタログ登録済みsourceへのリンク、後者は自由記述を含む。両者を同一の「使用済み」事実にはせず、プロトコル本文由来のsource名・usageとは引き続き分離する。

## その他の構造上の所見

- 同名ヘッダーがある: `Study ID, other`は2列、`Data characterisation details`は3列、`Procedure of data extraction`と`Procedure of results generation`は各2列。通常の`DictReader`は同名列を上書きする。このMCPが索引化する臨床項目には重複がないが、import結果に警告を返す。
- `DARWIN EU® study`はYes 112件、No 3,200件。Study typeとは独立している。
- Study statusはFinalised 1,942件、Ongoing 922件、Planned 423件、Discontinued 16件、Cancelled 9件。
- 定型designはCohort 1,634件、Other 607件、Cross-sectional 187件、Case-control 82件などで、複数選択はセミコロン区切り。
- Data collection methodsはSecondary use of data 1,367件、Primary data collection 623件、Combined 209件、No individual level data 9件、空欄1,104件。
- `Updated`は全件で解釈可能。2026-09-12基準で直近7日16件、30日111件、90日268件が更新されており、更新は特定日への一括処理ではなく継続的に発生している。

## 実装へ反映した改善

1. CSVの区切り文字だけを推定し、二重引用符規則を固定した。修正前はこの実CSVを14レコード目で列ずれとして拒否していた。
2. `Title`と`Data source(s)`の実ヘッダーを正式に対応した。`Other linked data sources`もsource名候補として統合する。
3. 定型designに`Non-interventional study design, other`を併記する。
4. ATC、INN/common name、疾患、outcome、目的、population、自由記述designなど、公開された臨床metadataを連絡先と分離したFTS補助テーブルへ保存する。商品名・コード・疾患・outcomeからPDF候補を探しやすくなる。
5. Studies CSVにData source type列がない場合はimport結果で警告する。Data source typeは選択研究のdetail pageで取得する。

## 今後の改善候補

- Data Sources用CSVとの結合可能性は[`data-source-linkage-20260912.md`](data-source-linkage-20260912.md)で検証した。Studyの`Data source(s)`主参照は全件一意に一致するが、Data Source側C5.1は資産の構成であり、Study側F8.7の使用分類を置き換えない。当時はC5.1を補助保存する案を検討したが、2026-09-13に[PDF由来の用途別分類](source-types.md)を採用した。Data Sources CSVの継続登録は不要。F8.7は選択研究のStudy detailから別途取得する。
- `Protocol file(s) - URI`を候補発見に使いつつ、Study documentsとの照合結果と版選択理由を記録する。CSVだけで最新版とは判定しない。
- 完全exportと絞込exportを区別するimport modeを追加する。完全snapshotの場合だけ、CSVから消えた古いレコードを削除またはtombstone化できるようにする。
- 重複ヘッダーを位置付きで保持する汎用profile toolを追加する。現状は検索に使わない重複欄を警告し、原本だけを保持する。
