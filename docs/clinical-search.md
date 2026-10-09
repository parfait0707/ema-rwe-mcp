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

1. 日本語の条件を英語の疾患・薬剤名、関連表現、適切なコード候補に展開し、概念ごとのブロック（`blocks`：`role`、`queries`、`category_terms`）にして`compare_protocols`へ渡します（手順の正本は[呼出元の手順](mcp-workflow.md)）。`plan_study_search`の`queries`と`code_searches`は、そのまま`search_studies`にも渡せる引数です。EMA医薬品辞書の記録が持つATCコードは`code_searches`に含めません（カタログでは同じコードが別の薬に付いていることがあるため）。
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

疾患名の辞書は同梱しません。日本語の質問は、MCPクライアント（Claude Code、Codex等）が`plan_study_search`の`client_expansion.instruction`に従って英語名・言い換え・コード候補・類縁概念へ翻訳し、`compare_protocols`へ渡します。この指示は**ICD-10（WHO 2019）を手がかりに類義語を探索する**よう求めます：該当する3桁分類・4桁細分類の名称と包含用語を類義語とし、Excludes（除外）の語は類義語にしない。同じブロックの別分類は`sibling`、ブロック・章は`broader`の類縁概念とする。クライアントが出すコードは検証されていない検索ヒントです。

`plan_study_search(use_llm=true)`は設定済みLLMに、同じICD-10の指針で英訳・関連語・コード候補・類縁概念の提案を依頼します。`research_protocol`の内部探索も検索アクションにコードを指定できます。LiteLLMの設定は[追加探索ガイド](exploration.md)を参照してください。LLMが提案するコードは常に`origin=llm, verification=unverified`です。コード候補のJSONには体系・版・ラベル・関係・出典URL・由来・確認状態・検索表記を残します。これは検索用メタデータで、PDFから抽出した根拠とは別です。

独自の言い換えやマスターを使う場合は、`data/dictionaries/`（wheelインストール時はユーザーデータフォルダ内の同名フォルダ。Git管理外）に概念辞書のJSONを置きます。フォルダ内の`*.json`はすべて読み込まれます。`EMA_TERMINOLOGY_PATH`でファイルまたはフォルダを指定することもできます。読み込み中の辞書は`catalogue_status.dictionaries`と`plan_study_search.dictionaries`で確認できます。書式は`data/terminology.example.json`（49概念、既定では読み込まない記入例）を参照してください。記入例はWHO ICD-10の分類名称を含まず、コードと出典URLだけを残しています。記入例の`related_terms`のうちカタログ由来の語は`scripts/mine_terminology.py`がカタログ本文から統計的に抽出した候補を判定して追記したもので、判定記録は`data/terminology_decisions.json`、手順は[関連語導出の記録](research/202609140758_terminology_mining.md)にあります。MedDRAやSNOMED CTのようなライセンス付きマスターは、各自の購読・ライセンスで得た内容から辞書を作り、このフォルダにだけ置いてください（[用語体系の比較](research/202609270400_terminology_sources.md)）。

### 類縁概念と一致語の出所

`analogous_terms`（`{term, relation}`）は、依頼概念とは**別の**臨床概念です。`relation`は`broader`（上位概念）、`sibling`（同じ上位概念に属する別疾患）、`associated`（合併症・関連病態）のいずれかです。通常の検索には含めず、依頼概念の研究が0件のときの`analogous_fallback`と、`match_scope=analogous`での明示的な検索にだけ使います。`related_terms`には同義語・表記揺れ・下位型・検索ヒントだけを置きます（例：1型糖尿病の`diabetes mellitus`は`related_terms`ではなく`analogous_terms`の`broader`）。

検索結果の各行は`match_basis`（`concept`／`analogous`）、`matched_terms`（実際に一致した展開語）、`matched_term_sources`（語の出所：`query`＝検索クエリ文字列そのもの（クライアントが渡した英語名を含む）、`caller`＝クライアント生成、`llm`＝サーバー側LLM（`use_llm=true`）の提案、`vocabulary`＝デザイン語彙、`dictionary:<ファイル名>`、`ema_medicines`＝EMA医薬品辞書、`catalogue_atc`＝カタログの「(ATCコード) 名称」から加えた医薬品名・クラス名、`category`＝`category_terms`の包括語）を持ちます。類縁概念で見つかった研究の定義は、類縁概念の定義として提示してください。辞書にない疾患は、呼出元が類縁概念を`analogous_terms`引数で渡せます。

疾患・医薬品以外の語は、`vocabulary.py`の同義語グループが英語へ展開します。対象は薬剤疫学プロトコールに頻出する研究デザイン・手法・集団の語です（例：交絡→confounding、インデックス日→index date、症例対照→case control / nested case control、小児→paediatric / pediatric / children、データリンケージ→record linkage）。疾患名や臨床語はここに置きません。以前あった広義の臨床語（diabetes、bleeding、cancer）は、「1型糖尿病」の中の「糖尿病」に反応して糖尿病全般の研究を拾うため削除しました。臨床語の翻訳はクライアントか利用者の辞書が担います。

国の絞り込み（`filters.countries`）は、同梱カタログで研究数の多い上位30か国について日本語名と一般的な英語の別称を受け付けます（例：ドイツ→Germany、イギリス／UK→United Kingdom、韓国→Korea, Republic of）。それ以外の国名はカタログ表記のまま指定してください。

### 未収録語のログ

利用者の辞書を設定しているときに限り、日本語を含む質問・検索語のうち、その辞書にも医薬品辞書にも一致しなかったものは`data/terminology_unmatched.json`（`EMA_UNMATCHED_LOG_PATH`で変更可。Git管理外）に件数・初回・最終・呼び出したツール名付きで記録されます。`plan_study_search`の`unmatched_logged`と`catalogue_status`の`unmatched_terms`（件数上位20件）で確認できます。英語だけの検索語は索引が英語なので記録しません。頻出する語を利用者の辞書に概念として追加してください。辞書がない既定の状態では、日本語の質問はすべてクライアントが翻訳するため記録しません。`EMA_TERMINOLOGY_PATH`にUTF-8 JSONファイルの絶対パスを設定すると、対象データに合わせた辞書へ差し替えられます。例：

```json
[
  {
    "concept_id": "local_ild_terms",
    "input_terms": ["間質性肺疾患", "間質性肺炎"],
    "english_terms": ["interstitial lung disease"],
    "related_terms": ["interstitial pneumonia"],
    "analogous_terms": [{"term": "pulmonary fibrosis", "relation": "sibling"}],
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

サーバーは疾患名とICD-10の対応を持ちません。対応づけはクライアントの知識、または利用者の辞書に依存し、検証されていません。ICD-10／国別修正版のコード指定、小数点有無の展開、ATCの第1〜5レベルのコード指定に対応し、形式を検証します。形式検証はマスター上の実在確認ではありません。WHO ATCの索引は同梱しません。ATCコードの名称はカタログ自身の「(ATCコード) 名称」の記載（例：`(B01AF02) apixaban`）から引き、クラスの所属薬はその記載とEMA医薬品辞書の記録が持つATCコードから名前で集めます（後述）。全疾患・全医療マスターの照会や階層の全子孫コード展開は未実装です。

通常の研究検索はインポート済みのカタログ情報と保存済み抽出結果が対象です。カタログ情報には疾患名もコードもなく、未取得PDFにしか書かれていない研究は、この検索だけでは発見できません。例外として、カタログの医薬品欄が空の研究の一部には、補完コマンドで題名などの医薬品名やプロトコルのPASS情報表の医薬品欄を補っています（後述）。候補条件を広げてPDFを取得する必要があります。保存PDFの追加検索は指定IDの全抽出テキストを対象にしますが、画像だけのコード表にはOCRが必要です。コード命中、原文引用の一致、章判定だけで医学的同等性やアウトカムとしての使用を保証するものではありません。

## 商品名 ⇄ INN／common name ⇄ ATC

### EMA医薬品辞書

`refresh_drug_dictionary()`（CLI: `ema-rwe refresh-drugs`）で[公式EMA医薬品JSON](https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format)を取得します。ヒト用医薬品の`name_of_medicine`、`international_non_proprietary_name_common_name`、`atc_code_human`を対応付け、出典URLと更新日を保持します。EMAの欄名がINN／common nameなので、すべてを厳密なWHO INNと断定せず、その区別をJSONにも残します。

成分名の末尾の塩・エステル・水和物の語（`hydrochloride`、`choline`、`diolamine` など）は、基名でも製品を引ける検索キーを足すだけで、製品の成分の組み合わせは変えません（`SALT_WORDS`）。別の製品系列を分ける語（`disoproxil` と `alafenamide` など）は塩とみなしません（`NOT_SALT_WORDS`）。辞書を更新すると、どちらの一覧にも無い末尾の語のうち「X W」と「X」の両方が成分名にあるものを `salt_word_candidates` として返します。一覧への追加は、出典と検索の変化を添えたコードレビューで行い、自動では加えません。

保存先は`EMA_DRUG_DICTIONARY_PATH`、未設定時はDBと同じフォルダの`ema-medicines.json`です（チェックアウトとwheelには`data/ema-medicines.json`を同梱）。7日間は再利用し、`force=true`で再取得できます。取得した原本とSHA256を利用し、不正な応答で旧辞書を上書きしません。通常の研究・PDF検索はローカル辞書を読み、通信しません。未取得・7日経過は`clinical.drugs.needs_refresh`に表示します。

### 名前で照合する（v1.1）

医薬品の照合の主キーは名前です。ATCコードは名前どうしをつなぐ内部の手がかりとしてだけ使い、情報源をまたいでコードで照合しません。カタログの検索に使うのは名前と、カタログに記載のある第4レベルのクラスコード（`category_terms`）だけです。

- **EMA医薬品辞書**：製品名とINN／common nameを、成分の組み合わせ全体が同じ製品どうしで双方向に展開します（例：`Eliquis`→`apixaban`、`apixaban`→`Eliquis`）。`compare_protocols`では、検索語を1つの医薬品名として語全体で照合し、長い語の一部では展開しません（GLP-1受容体作動薬の語からglucagonの製品へは展開しない）。`search_studies`の5語以上の自由文では、文中の医薬品名も拾います。
- **名前の比べ方**：名前は単語の単位で比べ、単語の内部の文字列では照合しません（fosaprepitantはaprepitantと別の薬）。名前を成分に分けるのは括弧の外の区切り（`/`、`+`、`,`、`;`、`and`、`with`）だけで、`papillomavirus (human types 16, 18)`は1成分です。製品名の末尾の`(previously ...)`を外した名前（`Icandra`など）も、名前全体の照合で引けます（成分の組が異なる製品にまたがる名前は使いません）。
- **塩の語**：検索語や辞書の名前の末尾の塩・エステル・水和物の語（citrate、maleate、hydrochlorideなど。カタログとEMA辞書に「X W」と「X」がともに現れる語Wから作った一覧）は、語全体の照合のときだけ除いて照合します（例：tofacitinib citrate→tofacitinibの製品、dabigatran etexilate→dabigatran）。検索の手がかりで、塩と遊離体を同じ製剤とはみなしません。
- **辞書のATCコード**：EMA辞書の記録が持つATCコードは、カタログの検索語や`code_searches`にしません（辞書のコードがカタログでは別の薬に付いていることがあるため）。取得したプロトコルPDFの本文検索（`search_protocol_text`）のヒントにだけ使うので、PDF本文にINNやATCしかない箇所は見つけられます。
- 検索結果の`query_expansion.clinical.drugs`に候補製品、成分構成、出典、辞書版、件数と打切りの有無を返します。

### カタログの記載による成分とクラスの展開（v1.0）

カタログのexposures欄は医薬品を`(B01AF02) apixaban`や`(N03A) ANTIEPILEPTICS`のように「ATCコード 名称」で書くことがあります。`compare_protocols`はこの組とEMA辞書から、医薬品の検索語を名前へ展開し、加えた語を`medicine_expansion`（1問あたり100語まで。超過分は`omitted`）に返します（`medicines.expand_medicine`）。

- **成分・製品**（第5レベル）：カタログ上の名称が同じ薬を指せば検索語に、広い名称（問い合わせにある株や型を書かない名称）なら`category_terms`に加え、狭い名称（記録に無い株や型を書く名称）なら加えません。上位の第4レベルのクラス（コードと名称）を`category_terms`に加えます。カタログにそのクラス自身か別の所属薬の記載があるときだけです。
- **クラス**（カタログのクラス名称と一致する語、または第3・第4レベルのATCコード）：クラスの名称と、カタログとEMA辞書でそのクラスに属する薬の名前を検索語に加えます。所属薬は同義語ではなく、クラスの一部として扱います。カタログがそのコードを別の名前で記録しているEMA辞書の記録は加えず、`omitted_members`（名前、コード、カタログの名称、理由。件数で切らない）と`omitted_members_total`に返します（100語の上限で切った語の数`omitted`とは別）。名前がATCの名称と違うだけの所属薬と辞書のコードの誤りが混ざるので、呼出元がクラスに属すると判断したものを検索語に加えて検索し直します。
- 合わせ剤は成分集合全体で扱います（Janumet→sitagliptin / metformin。sitagliptin単剤のJanuviaとは同一扱いしない）。`metformin`が`metformin and empagliflozin`に解決されることはありません。
- 依頼した薬の研究が0件で、呼出元が第5レベルのATCコードを`queries`に渡していた場合は（概念が1つの検索のときだけ）、同じクラス（第4レベルに名称が無ければ第3レベル）とカタログ上の所属薬を類縁概念（`broader`／`sibling`、出所`catalogue_atc`）として示します。

日本語の医薬品名は内蔵していません。呼出元LLM、または`plan_study_search(use_llm=true)`が英語名を渡すと、上記の辞書とカタログで展開します。呼出元には、INN、EU・米国の製品名、略語（TNFi、DOACsなど）、語形やハイフン表記の変種を渡すよう`client_expansion`で指示し、カタログにもEMA辞書にも無い薬には第5レベルのATCコードも求めます。出典のある日本語対応表を使う場合は、利用者の辞書（`data/dictionaries/`）の概念に日本語名を`input_terms`、INNを`english_terms`として登録します。医薬品辞書が見つからない場合は、EMA辞書による製品名⇄INNの展開を行わず`needs_refresh=true`を返します。カタログの記載による展開は続けます。

同じINNでも用量・投与経路・適応が一致するとは断定しません。EMA辞書の収載範囲は中央審査品目で、国ごとの全商品名や全ATC割当てを網羅しません。

### 医薬品欄が空の研究の補完（v1.2、v1.3）

カタログの医薬品欄が空の研究には、利用者が実行する`ema-rwe backfill-protocols`で医薬品を補っています。補った値はカタログとは別の`protocol_observations`表にあり、索引の医薬品の列（名前と記録したATCコード）で検索され、候補の`observed_exposures`に出所付きで返ります。同梱DBは、医薬品欄が空の712件のうち215件を補った結果を含みます。

- **`catalogue_text`**：題名・説明・目的に書かれた既知の医薬品名（カタログの第5レベルの名称、EMA辞書のINNと1語の製品名）。研究の略称（`SONATA study`）、測定される物質（`fractional exhaled nitric oxide`）、受容体や阻害薬のクラス名の一部（`angiotensin II receptor blockers`）は除きます。比較対照や除外基準の薬であることがあります。
- **`protocol_pass_table`**：プロトコルのPASS情報表の「Active substance」「Medicinal product」欄にある既知の医薬品名、カタログのクラス名、欄に書かれたATCコード（ページ付き）。辞書に無い薬は、欄のATCコードで記録します。欄の自由記述は保存しません。プロトコル本文のATCコードは、除外基準・アウトカム・共変量にも使われるため記録しません。

どちらも検索の手がかりで、その研究で曝露として使われたかはプロトコルで確かめます。詳細は[仕様 v1.2](spec/v1.2.md)・[v1.3](spec/v1.3.md)を参照してください。
