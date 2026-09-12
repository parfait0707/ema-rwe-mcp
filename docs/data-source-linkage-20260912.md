# Data Sources CSVとStudies CSVの結合検証（2026-09-12）

## 対象

- Studies: `data/imports/studies/20260912_export-data.csv`
- Human Data Sources: `data/imports/data-sources/20260912_rwd-catalogues-data-source-human-export-public.csv`
- Data Sources SHA256: `28f2f95a9532b8bd9153e1b787e8629ade3e6ba95b80f7334042fc5053457825`

原本には連絡先欄があるため両CSVともGit管理しない。この文書には集計値だけを記録する。

## Data Sources CSVの品質

Data Sources CSVはUTF-8 BOM付き、カンマ区切り、131列、286行。全行の列数は131で、全件がHumanかつ`Outdated=No`だった。

| 項目 | 欠損件数 | 欠損率 |
|---|---:|---:|
| Data source ID | 0 | 0.00% |
| Data source name | 0 | 0.00% |
| URL LINK | 0 | 0.00% |
| Data source domain | 0 | 0.00% |
| First published | 0 | 0.00% |
| Date of last update | 0 | 0.00% |
| Data source type | 1 | 0.35% |
| Data source acronym | 22 | 7.69% |
| Data source countries | 1 | 0.35% |
| Data source website | 36 | 12.59% |
| PURI | 286 | 100.00% |

Data source ID、正式名とも空欄・重複・形式不正は0件。正規化後の名称衝突も0件だった。同名ヘッダーは`Age group`、`Population size`、`Active population size`が各2列あるが、今回の結合項目には影響しない。

Data source typeは複数選択可能で、主な値はOther 106、Disease registry 97、Hospital inpatient records 76、Hospital outpatient visit records 64、Primary care medical records 51、Pharmacy dispensing records 49、Hospital discharge records 43、Administrative healthcare records (e.g., claims) 42だった。

## Studiesとの結合結果

Studies CSVの`Data source(s)`をセミコロン・改行・パイプで分割し、Unicode正規化、大小文字・句読点・登録商標記号を無視した名称で照合した。正式名称に一致しない場合だけData source acronymを使った。

| 指標 | 結果 |
|---|---:|
| 全Study | 3,312 |
| `Data source(s)`を持つStudy | 857 |
| `Data source(s)`内のsource参照 | 2,004 |
| 正式名へ一意に一致した参照 | 2,004（100.00%） |
| 全参照が一致したStudy | 857（100.00%） |
| 一致sourceからtypeを得られたStudy | 830 |
| typeを得られなかったStudy | 27 |

typeを得られなかった27件はいずれも`Other data source`だけに紐づき、そのData Sourcesレコードのtypeが空欄だった。

`Other linked data sources`には1,174個の自由記述断片があり、正式名25件、acronym 47件の合計72件だけが一意に一致した。これを補助リンクとして使うと、追加15 Studyを結合でき、type取得可能Studyは845件になる。ただし自由記述欄なので、主結合とは区別して低い確度で扱う。

## source typeによる絞込

claimsはData Sources CSVの公式type`Administrative healthcare records (e.g., claims)`をそのまま使える。該当Data Sourceは42件で、`Data source(s)`の主結合だけでclaims sourceに紐づくStudyは329件だった。自由記述欄の確実な一致も加えると331件になる。

registryはtype名に`registry`を含むData Sourceが146件、drug dispensing/prescriptionは`Pharmacy dispensing records`が49件あり、同じ方法で絞り込める。

一方、Data Sources CSVのtypeには`EHR`または`Electronic health records`という値が0件だった。Primary care medical records 51件、Hospital inpatient records 76件、Hospital outpatient visit records 64件などをEHRへ対応させるには、MCP側で明示的な分類規則を定義し、「公式type」ではなく「MCPによる上位分類」として由来を表示する必要がある。

## 結論と制約

`Data source(s)`はData Sources CSVの正式名と完全に結合でき、claims・registry・pharmacy dispensingについては公式typeを用いた絞込が可能である。ただし、Study全体の74.12%には`Data source(s)`がない。結合できないStudyを非claimsと扱うと偽陰性になるため、フィルタ結果は`matched`、`non-matching`、`unknown`を区別する必要がある。

実装時はData source IDを内部キーにし、以下を検索結果へ出す。

- Study detail pageに直接表示されたData source type
- Data Sources CSVとの主結合から得た公式typeとData source ID
- `Other linked data sources`から得た補助一致とmatch method
- typeが得られない理由と`unknown`件数
- EHRなどMCPが上位分類した場合の規則とprovenance
