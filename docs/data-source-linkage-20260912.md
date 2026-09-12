# Data Sources CSVとStudies CSVの結合検証（2026-09-12）

> 2026-09-13追記：本書は過去の結合検証と当時の提案です。実装方針は[PDFによる用途別分類](source-types.md)へ変更しました。Data Sourcesインポーター・フィルター付きCSV登録は必須にせず、元CSVフォルダは依頼により削除済みです。以下の分類ルールを現行の検索仕様として適用しないでください。

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

### 同名フィルターの意味はrecord typeごとに異なる

EMAの[公式metadata一覧](https://www.ema.europa.eu/en/documents/other/list-metadata-hma-ema-catalogues-real-world-data-sources-studies_en.pdf)と[User Guide](https://www.ema.europa.eu/en/documents/regulatory-procedural-guideline/user-guide-hma-ema-catalogues-real-world-data-sources-studies_en.pdf)では、Study側はF8.7 `Data sources (types)`、Data Source側はC5.1 `Data source type`という別フィールドである。

- F8.7は、そのStudyで使うデータソースを研究登録者が分類する。`Electronic healthcare records (EHR)`、`Drug prescriptions`、`Published literature`などを含む。
- C5.1は、登録されたデータ資産自体をデータ保有者が分類する。Administrative、Primary care、Secondary care、Registries等の下に、claims、primary care medical records、hospital inpatient recordsなどがある。

したがって、Data Sourceでclaimsを選んだ42件は「claims型と登録されたデータ資産の数」、Studyでclaimsを選んだ831件は「claimsを使うとStudy側で申告された研究の数」であり、同じ母集団ではない。42 Data Sourcesが複数Studyから再利用されるため、Study数が42を超えること自体も自然である。

claimsはC5.1の公式type`Administrative healthcare records (e.g., claims)`をそのまま使える。該当Data Sourceは42件で、`Data source(s)`の主結合だけでこのような資産に紐づくStudyは329件だった。自由記述欄の確実な一致も加えると331件になる。

ただし、この329件を「claimsを使用したStudy」と断定してはならない。1つのData Sourceはclaims、dispensing、hospital records等を同時に持ち得るが、個別Studyがその全構成要素を使うとは限らない。C5.1から分かるのはリンク先資産の能力・構成であり、Studyでの使用分類はF8.7、実際の解析対象はプロトコルで確認する。

registryはtype名に`registry`を含むData Sourceが146件、drug dispensing/prescriptionは`Pharmacy dispensing records`が49件あり、同じ方法で「その構成を持つ資産へリンクしたStudy候補」を作れる。Studyでの使用を確定する絞込ではない。

一方、C5.1には`EHR`または`Electronic health records`という選択肢がない。Primary care medical records 51件、Hospital inpatient records 76件、Hospital outpatient visit records 64件などをEHRへ対応させることは可能だが、EMAの公式対応関係ではない。MCPで行う場合は「MCPによる上位分類」として規則と由来を表示する。

## 採用する判定規則

Studyのsource-type検索ではF8.7を第一の判定根拠とし、C5.1による結合結果は補助候補に限定する。プロトコル抽出は別層に維持する。

| 状態 | 判定 | 絞込での扱い |
|---|---|---|
| `study_reported_match` | Study F8.7が指定typeを含む | Study検索の確定候補 |
| `study_reported_nonmatch` | F8.7を確認済みで指定typeを含まない | 指定typeから除外可能。ただしlinked sourceのtypeと違う場合は差異を表示 |
| `linked_source_possible` | F8.7不明だが、結合先C5.1が指定typeを含む | 補助候補。使用を断定しない |
| `linked_type_missing` | 登録sourceへ結合したがC5.1が空 | `unknown`として保持 |
| `unresolved_free_text` | `Other linked data sources`だけで一意に結合不能 | `unknown`として保持 |
| `no_source_metadata` | Studyのsource名・F8.7とも未取得 | `unknown`として保持 |
| `protocol_verified` | プロトコルに使用対象と根拠がある | 最終比較でusageと引用を提示 |

Study全体の内訳は、登録source名を持つ857件、自由記述sourceだけを持つ510件、どちらのsource名もない1,945件である。後二者を非claimsとして除外してはならない。

現在のStudies CSVにはF8.7列がないため、このCSV単体ではサイト上の831件を再現できず、329件との包含関係も検証できない。完全なStudy-level絞込には、次のいずれかが必要である。

1. EMAがF8.7をStudies exportへ含める。
2. Study側でtypeを指定した公式filtered exportについて、filter条件とStudy ID集合をsnapshot metadataとして保存する。
3. 対象StudyのData managementページからF8.7を取得してキャッシュする。

2と3を組み合わせる場合も、候補数は`study_reported_match`と`unknown`を分けて示す。`unknown`を黙って除外した件数を「全EMA Studyを対象とした結果」と表現しない。

## 保存するprovenance

実装時はData source IDを内部キーにし、以下を検索結果へ出す。

- Study F8.7に直接表示されたtype、取得元URL、確認日時
- Data Sources CSVとの主結合から得たC5.1 typeとData source ID
- `Other linked data sources`から得た補助一致とmatch method
- F8.7とC5.1の一致・差異。どちらかで他方を上書きしない
- typeが得られない理由、`unknown`件数、判定対象の分母
- EHRなどMCPが上位分類した場合の規則とprovenance
