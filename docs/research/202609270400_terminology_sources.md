# 類縁概念の根拠とする医療用語体系の比較（SNOMED CT・MedDRA・MeSH・ICD-10）

## 調査目的

`data/terminology.json` の `analogous_terms`（broader／sibling／associated）は、出典のないモデル判断で分類された（spec v0.5）。根拠となる用語体系を選ぶため、次の 4 点を調べた。

- SNOMED CT、MedDRA、MeSH、ICD-10 の用途
- 階層の性質
- 本リポジトリ（GitHub で PUBLIC、MIT License）で使えるかどうかを決めるライセンス条件
- EMA RWD カタログでの出現状況

## 前提と制約

- 本リポジトリは派生データ（辞書 JSON）をコミットして配布している。そのため、「使えるか」だけでなく「公開リポジトリで再配布できるか」を分けて扱う。
- 利用者は日本在住の研究者を想定する。
- 以下のライセンス記述は 2026-09-27 時点で取得した公開ページに基づく。法的判断ではない。

## 調査方法・参照元（一次資料）

- WHO「FAQ Licensing ICD-10」PDF（日付の記載なし）: https://cdn.who.int/media/docs/default-source/publishing-policies/copyright/who-faq-licensing-icd-10.pdf
- WHO Copyright ページ: https://www.who.int/about/policies/publishing/copyright
- NLM MeSH Terms and Conditions: https://www.nlm.nih.gov/databases/download/terms_and_conditions_mesh.html
- NLM MeSH Tree Structures: https://www.nlm.nih.gov/mesh/intro_trees.html
- SNOMED International Members: https://www.snomed.org/members
- SNOMED CT Vendor Introduction, Licensing: https://docs.snomed.org/snomed-ct-practical-guides/vendor-introduction-to-snomed-ct/7-licensing
- SNOMED Other products（Global Patient Set）: https://www.snomed.org/other-snomed-products
- MedDRA Factsheet 1「Accessing MedDRA」（2026-07-08 版）: https://files.meddra.org/www/Website%20Files/Fact%20Sheets/meddra_factsheet1_accessing_meddra-8%20July%202026-A4.pdf
- The Book of OHDSI, Standardized Vocabularies: https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html
- DARWIN EU Phenotype library（最終更新 2023-03-19）: https://darwin-eu.org/methods/phenotype-library
- 同梱カタログ `data/ema.sqlite3`（3,312 研究）の FTS で、各体系名の出現研究数を数えた。

## 主な発見

### 各体系の性質

| 体系 | 設計目的 | 階層 | 主な利用場面 |
|---|---|---|---|
| ICD-10（WHO） | 死因・疾病統計の分類。WHO FAQ は、疫学、発生率・有病率の監視、診療報酬、ケースミックスでの使用を挙げる | 章 → ブロック → 3 桁 → 4 桁の**単一階層**（各コードの親は 1 つ） | レセプト・入院データの傷病名コード。各国に修正版がある（ICD-10-CM、-GM、-AM など。WHO は修正版に責任を持たない） |
| MeSH（NLM） | MEDLINE の文献索引と検索 | 16 カテゴリ（C＝疾患）で最大 13 階層。1 つの記述子が複数の位置に現れる**多重階層**。同義語（entry terms）が豊富 | 文献検索。患者データのコーディングには使われない |
| SNOMED CT | 臨床記録用の用語体系 | is-a による**多重階層**。粒度が細かい | OHDSI/OMOP の病名（condition）の標準語彙。DARWIN EU は OMOP CDM で統一している。英国のプライマリケア記録でも使われる |
| MedDRA（ICH） | 規制当局と企業の間で安全性情報を交換するための用語 | SOC → HLGT → HLT → PT → LLT。PT は複数の SOC に属しうる。関連する PT の束として **SMQ（Standardised MedDRA Queries）**がある | 臨床試験と市販後の有害事象コーディング。OMOP では病名領域の分類概念として使われる |

### ライセンス（公開リポジトリでの再配布が論点）

| 体系 | 利用 | 公開リポジトリでの再配布 |
|---|---|---|
| MeSH | 無償（"No charges, usage fees or royalties"） | 可。条件は、NLM を出典として明示すること、NLM の推奨を示唆しないこと、最新版を維持するか使用版を明示すること |
| ICD-10 | WHO が著作権者。WHO FAQ は商用・非商用／研究・内部利用の各ライセンスを案内し、非商用・研究用には登録制のライセンスがある | **要確認**。FAQ は、ソフトウェアへのコードと名称の組み込みをライセンスの対象として扱う。WHO Copyright ページが CC BY-ND 3.0 IGO と明記しているのは ICD-11 などで、ICD-10 については一次資料で確認できなかった。**本リポジトリは現在、ICD-10 のコードと WHO の名称（label）を `terminology.json` に含めて配布している** |
| SNOMED CT | 加盟国では無償。**日本は非加盟**（54 加盟国の一覧にない。EU でもブルガリア、ギリシャ、ポーランド、ルーマニアは非加盟）。非加盟地域での利用は事前に MLDS へ報告し、料金がかかる場合がある。承認された研究には減免がありうる | 階層を含む本体は、Affiliate License の枠外で公開再配布できない。Global Patient Set（GPS）は CC BY 4.0 で無償だが、**関係・階層を含まない**（ID、FSN、優先語、状態だけ） |
| MedDRA | 規制当局と非営利・非商用組織（教育機関、医学図書館、非営利活動の組織）は無償。日本の購読は JMO が担当し、料金表は JMO が定める | 購読規約の再配布条項は、今回の一次資料では確認できなかった（**未確認**）。購読制であることから、用語の公開再配布は制限される前提で扱うべき |

### EMA RWD カタログでの出現（同梱 3,312 研究、カタログ本文の FTS）

- ICD-10：95 件
- MedDRA：81 件
- ATC：74 件
- ICD-9：62 件
- OMOP：61 件
- Read codes：18 件
- SNOMED：16 件
- MeSH：**0 件**

カタログ本文だけの数え方で、プロトコル PDF の記載は含まない。

## 選択肢と比較（本 MCP の類縁概念の根拠として）

- **MedDRA**：有害事象アウトカムには最適。SMQ はもともと「関連する事象を束ねて拾う」ための道具で、「類似アウトカム」の考え方にそのまま合う。HLT を共有する PT どうしは sibling、HLT・HLGT は broader として使える。ただし購読が必要で、公開配布はできない前提になる。
- **SNOMED CT**：DARWIN EU／OMOP の研究定義と同じ語彙なので、concept set との照合には最も直接的。ただし日本は非加盟で、階層は公開配布できない。GPS には階層がない。
- **MeSH**：疾患の上位・兄弟関係と英語の同義語を、出典付きで公開配布できる唯一の候補。ただし文献索引のための語彙で、プロトコルの定義には使われない（カタログでの出現 0 件）。検索語を広げる根拠として使い、定義の同等性の根拠にはしない。
- **ICD-10**：レセプト・入院データの定義と同じ粒度で、ブロック・3 桁分類の兄弟関係は「同じコード範囲で定義されうる近縁疾患」を表す。ただし単一階層で粗く、関連病態（associated）は表せない。現行辞書の配布にライセンス上の未確認事項がある。

## 結論・推奨

1. **役割で使い分ける。**
   - 疾患・集団の上位／兄弟関係 → MeSH（公開配布可、出典明示）。
   - レセプト定義の近縁性 → ICD-10 のブロック・3 桁分類。
   - 有害事象アウトカム → MedDRA（HLT／HLGT／SMQ）。
   - DARWIN EU 研究の定義照合 → SNOMED CT（OMOP）。
2. **ライセンス付きの体系はリポジトリに入れない。** MedDRA と SNOMED CT は、利用者が自分の購読・ライセンスで得たファイルをローカルのパスで読み込む方式にする（`EMA_TERMINOLOGY_PATH` と同じ考え方）。コミットするのは読み込みコードだけにする。
3. **associated（合併症・関連病態）は階層から導けない。** MedDRA の SMQ に根拠がある場合を除き、`verification=unverified` として出典を空のままにし、専門家確認の対象とする。
4. **現行辞書の ICD-10 名称の配布について**、WHO の非商用・研究ライセンスへの登録か、ICD-11（CC BY-ND 3.0 IGO と明記）への切り替えを検討する。

## 未解決事項

- ICD-10 のライセンスは、CC BY-ND とする二次情報と、ライセンス制とする WHO FAQ が食い違っている。WHO（licensing@who.int）への確認が必要。
- MedDRA 購読規約の再配布条項と、JMO での日本の非営利研究者の扱い（料金・条件）が未確認。
- SNOMED CT の研究目的の減免（"approved research projects"）の要件が未確認。
- DARWIN EU の phenotype 定義が実際に SNOMED の concept set で公開されているか、公開ページでは確認できなかった。
