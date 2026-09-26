# 日本語の臨床概念から英語・医療コードへの検索

`plan_study_search(question)` は検索を実行せず、呼出元が確認・実行できる検索計画を返します。
例：「間質性肺疾患をアウトカムとした研究を探し、どのようなアウトカム定義を使ったかを教えて」では、次を独立した検索候補にします。

| 種類 | 検索語 |
|---|---|
| 英語名 | interstitial lung disease / interstitial pulmonary disease / ILD |
| 関連表現 | interstitial pneumonia |
| コード候補 | ICD-10 J84.9、表記揺れ J849 |

[WHO ICD-10 2019](https://icd.who.int/browse10/2019/en/GetConcept?ConceptId=J84.9) のJ84.9は詳細不明の間質性肺疾患で、包含語にInterstitial pneumonia NOSがあります。広い疾患概念との完全同義ではなく、`relation=unspecified_subtype`として扱います。この1コードがILD全体を網羅するという意味ではありません。ICD-10-CMなど国別修正版とも区別します。

## 呼出元LLMの手順

1. 日本語の条件を英語の疾患・薬剤名、関連表現、適切なコード候補に展開。`plan_study_search`の計画を補い、`queries`と`code_searches`をそれぞれ検索します。後者はそのまま`search_studies`に渡せる引数です。
2. 候補研究の最新PDFを取得し、共通項目を抽出・保存。結果の`protocol_id`を使ってPDF全文を追加検索します。
3. 疾患名で見つからなくてもコード単独で検索。Methods、outcome definitions、コード表・付録を読み、前後の章と親章を確認します。
4. コードがアウトカム・曝露・併存疾患・除外基準のどれに使われたか、対象データソース、コード体系と版、回数・期間・除外条件を本文に基づいて回答します。不明な情報は不明として残します。
5. PDFのページ・章・原文引用を添えて`cache_protocol_answer`に保存します。検索候補のコードを、研究が採用した定義として保存してはいけません。

`search_studies`と`search_protocol_text`は例えば次の引数を受け付けます。

```json
{
  "query": "interstitial lung disease",
  "codes": [{"system": "ICD-10", "code": "J84.9", "vocabulary_version": "WHO 2019"}]
}
```

PDF検索にはさらに`protocol_id`を指定します。CLIも`--code "ICD-10:J84.9"`を複数指定できます。ICDの小数点有無を展開し、`J84.9`を単独の`9`に分割して検索しません。数値コードの先頭ゼロも保持します。SNOMED CT、OMOP concept_id、RxNormなどの数値は体系なしでは判別できないので、`system`を指定してください。体系間の自動変換は行いません。

## 内部LLMと辞書

`plan_study_search(use_llm=true)`は設定済みLLMに英訳・関連語・コード候補の提案を依頼します。`research_protocol`の内部探索も検索アクションにコードを指定できます。LiteLLMの設定は[追加探索ガイド](exploration.md)を参照してください。LLMが提案するコードは常に`origin=llm, verification=unverified`です。コード候補のJSONには体系・版・ラベル・関係・出典URL・由来・確認状態・検索表記を残します。これは検索用メタデータで、PDFから抽出した根拠とは別です。

チェックアウト内では同梱の`data/terminology.json`（49概念。日本語疾患名→英語名・関連語・WHO ICD-10 2019の分類コード、出典URL付き）が既定で読み込まれます。コードは検索用の候補であり`verification=unverified`のままです。`related_terms`のうちカタログ由来の語は、`scripts/mine_terminology.py`がカタログ本文（Outcomes、Medicinal condition、目的、タイトル）から統計的に抽出した候補を、明文化した採否規則と標本確認で判定して追記したものです。判定記録は`data/terminology_decisions.json`、手順と結果は[関連語導出の記録](research/202609140758_terminology_mining.md)を参照してください。

疾患・医薬品以外の語は、`vocabulary.py`の同義語グループが英語へ展開します。対象は薬剤疫学プロトコールに頻出する研究デザイン・手法・集団の語です（例：交絡→confounding、インデックス日→index date、症例対照→case control / nested case control、小児→paediatric / pediatric / children、データリンケージ→record linkage）。個別の疾患名や薬効群はここに置かず、概念辞書と医薬品辞書で扱います。

国の絞り込み（`filters.countries`）は、同梱カタログで研究数の多い上位30か国について日本語名と一般的な英語の別称を受け付けます（例：ドイツ→Germany、イギリス／UK→United Kingdom、韓国→Korea, Republic of）。それ以外の国名はカタログ表記のまま指定してください。

### 未収録語のログ

日本語を含む質問・検索語のうち、概念辞書にも医薬品辞書にも一致しなかったものは`data/terminology_unmatched.json`（`EMA_UNMATCHED_LOG_PATH`で変更可。Git管理外）に件数・初回・最終・呼び出したツール名付きで記録されます。`plan_study_search`の`unmatched_logged`と`catalogue_status`の`unmatched_terms`（件数上位20件）で確認できます。英語だけの検索語は索引が英語なので記録しません。頻出する語を`data/terminology.json`に概念として追加し、関連語は`scripts/mine_terminology.py`で導出してください。`EMA_TERMINOLOGY_PATH`にUTF-8 JSONファイルの絶対パスを設定すると、対象データに合わせた辞書へ差し替えられます。例：

```json
[
  {
    "concept_id": "local_ild_terms",
    "input_terms": ["間質性肺疾患", "間質性肺炎"],
    "english_terms": ["interstitial lung disease"],
    "related_terms": ["interstitial pneumonia"],
    "code_candidates": [
      {"system": "ICD-10", "code": "J84.9", "vocabulary_version": "WHO 2019",
       "relation": "unspecified_subtype",
       "source_url": "https://icd.who.int/browse10/2019/en/GetConcept?ConceptId=J84.9"}
    ]
  }
]
```

辞書は2 MB、3,000概念まで。独自辞書はサーバーがマスターと照合したわけではないので`local_dictionary / unverified`となります。内容のハッシュを質問別回答のキャッシュキーに含め、辞書変更後は以前の回答を自動再利用しません。

## 網羅性の限界

疾患名とICD-10の対応は`data/terminology.json`に収録した概念に限られます。ICD-10／国別修正版のコード指定、小数点有無の展開、ATCの第1〜5レベルのコード指定に対応し、形式を検証します。形式検証はマスター上の実在確認ではありません。ATCの第5レベルと医薬品名の対応は、後述の公式EMA医薬品辞書を利用します。全疾患・全医療マスターの照会や階層の全子孫コード展開は未実装です。

通常の研究検索はインポート済みのカタログ情報と保存済み抽出結果が対象です。カタログ情報には疾患名もコードもなく、未取得PDFにしか書かれていない研究は、この検索だけでは発見できません。候補条件を広げてPDFを取得する必要があります。保存PDFの追加検索は指定IDの全抽出テキストを対象にしますが、画像だけのコード表にはOCRが必要です。コード命中、原文引用の一致、章判定だけで医学的同等性やアウトカムとしての使用を保証するものではありません。

## 商品名 ⇄ INN／common name ⇄ ATC

`refresh_drug_dictionary()`（CLI: `ema-rwe refresh-drugs`）で[公式EMA医薬品JSON](https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format)を取得します。ヒト用医薬品の`name_of_medicine`、`international_non_proprietary_name_common_name`、`atc_code_human`を対応付け、出典URLと更新日を保持します。EMAの欄名がINN／common nameなので、すべてを厳密なWHO INNと断定せず、その区別をJSONにも残します。

保存先は`EMA_DRUG_DICTIONARY_PATH`、未設定時はDBと同じフォルダの`ema-medicines.json`です。7日間は再利用し、`force=true`で再取得できます。取得した原本とSHA256を利用し、不正な応答で旧辞書を上書きしません。通常の研究・PDF検索はローカル辞書を読み、通信しません。未取得・7日経過は`clinical.drugs.needs_refresh`に表示します。別環境でセットアップする場合は最初に更新ツールを呼んでください。

例：`Eliquis`→`apixaban`と`B01AF02`、`apixaban`→`Eliquis`等の同じ成分構成を持つ医薬品名、`B01AF02`→両方の名称。検索結果の`query_expansion.clinical.drugs`に候補製品、成分構成、出典、辞書版、件数と打切りの有無を返します。商品名の検索でも、本文にINNやATCしかない箇所を検索できます。ATCの上位クラスを単一成分と同義には扱いません。

日本語の医薬品名は内蔵していません。呼出元LLM、または`plan_study_search(use_llm=true)`が英語名を`synonyms`に渡すと、公式辞書で双方向展開します。出典のある日本語対応表を使う場合は、`EMA_TERMINOLOGY_PATH`の概念に日本語名を`input_terms`、INNを`english_terms`として登録します。一致した概念の英語名は医薬品辞書にも渡るため、同じ成分構成の製品名まで展開されます。医薬品辞書が見つからない場合は何も展開せず、`needs_refresh=true`を返します。

配合剤は成分集合全体を保持します。例：Janumet→sitagliptin / metforminであり、sitagliptin単剤のJanuviaと同一扱いしません。塩や製剤を自動的に同一化せず、同じINNでも用量・投与経路・適応が一致するとは断定しません。収載範囲はEMAの中央審査品目で、国ごとの全商品名や全ATC割当てを網羅しません。
