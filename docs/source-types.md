# PDFに基づくデータタイプと定義の比較

2026-09-13の実装。Data Sources CSVの結合は必須にしない。Studies CSVと保存済み解析から候補を発見し、選択した研究のプロトコルを呼出元LLM／内部LLMが読み、データの種類と用途を根拠付きで保存する。

## 問い合わせと候補数

```json
{
  "question": "NVAF患者のコホート定義を比較してください",
  "queries": ["NVAF", "non-valvular atrial fibrillation", "nonvalvular atrial fibrillation"],
  "darwin_only": false,
  "source_preference": {"types": ["claims"], "role": "cohort", "mode": "prefer"}
}
```

これは`compare_protocols`の引数。呼出元LLMは自然言語から希望タイプと定義の用途を指定する。`role`は`cohort`、`outcome`、`exposure`、`covariate`、`other`、`any`。複数の用途を尋ねる場合は`any`で取得し、回答では各用途を分ける。`any`の一致は研究中のいずれかの用途での一致であり、すべてのアウトカムが同じタイプで再現できる意味ではない。

`mode=prefer`は同じタイプを優先し、他タイプも補足として残す。`mode=only`はユーザーが限定を明示した場合だけ使い、該当する用途に明示的な根拠があり、他タイプとの連結を必要としない行だけを比較表の候補にする。推定・不明・非掲載の研究もJSONと一次判定一覧に残す。`only`は「使用予定」も含み、完了研究限定ではない。

`filters.data_source_types`／CLIの`--source-type`はカタログの種別タグ（`claims`／`ehr`／`registry`／`others`）でローカル候補を絞る条件で、`source_preference`と併用できる。タグはEMA検索画面でData source typeを限定したexportを`data/imports/source_type/<日付>_<種別>_export-data.csv`として取り込んだときに付与する（Studies exportに種別列はなく、ファイル名だけが根拠）。`others`は3種別のexportいずれにも含まれない研究で、種別exportを取り込む前はすべて`others`になる。取り込んだ種別は`catalogue_status.source_type_imports`で確認する。

一次判定上限を超えたら、`facets`の件数を示して種別と実施国の両方をユーザーに尋ね、回答を`filters`へ渡す。国・研究デザインも引き続きメタデータで絞る。候補数は全検索式の重複除去後のローカル一致数で、EMA全体や未取得PDFを網羅する数ではない。PDF由来の判定（`source_preference`）は絞り込み後の一次判定で行い、カタログのタグとは別に保持する。

## 抽出・追加探索

既存の`analyze_protocol`、`cache_protocol_analysis`、`research_protocol`、`cache_protocol_answer`を拡張した。新しい専用ツールは不要。共通抽出のschemaは`0.2`。共通抽出と質問別回答に`source_assessments`を持つ。

```json
{
  "value": "Example Claims Database",
  "types": ["claims"],
  "role": "cohort",
  "basis": "explicit",
  "usage": "planned",
  "definition": "診断コードによる患者抽出。具体的なコード、回数、期間は根拠の記載を保持する。",
  "requires_linkage": false,
  "evidence": [{"page": 12, "section": "8.2 Data sources", "quote": "ここには実際のPDFに存在する短い原文引用を入れる"}]
}
```

この例は架空の形式説明であり、実際の研究の根拠ではない。

- `value`はPDFに記載されたデータソース名。引用中の存在を検証する。
- `types`はclaims、ehr、registry、drug_dispensing_prescription、other。分類は重なり得る。未知のタイプをotherへ強制しない。
- 一つのソース構成要素と一つの定義用途につき一つのassessmentを保存する。claimsによるコホートとEHRによるアウトカムを同じ用途へ混ぜない。
- `basis=explicit`はタイプ・用途が本文で明示される場合。記述から推定する場合は`inferred`。名称、ICDコード、hospital recordsという語だけからclaims／EHRを断定しない。
- `requires_linkage=true`は、その定義が別タイプとの連結を必要とする場合。希望タイプを含むだけで単独再現可能とは扱わない。
- `usage`はused／planned／candidate／unclear。計画書の使用予定を使用実績へ変換しない。
- 不明な分類はassessmentを作らず、探索範囲・不足を`missing_information`へ書く。空配列だけでも適合性はunknownになるが、理由の記載を省略しない。

データソース、Methods、対象集団、アウトカム／曝露定義、コード付録、隣接章を同義語と章構造で探索する。呼出元方式では返却指示に従い全バッチ・必要な追加探索を完了して保存する。内部方式も同じスキーマと引用検証を使い、探索は既存の検索・章一覧・読解アクションとステップ上限に従う。

引用の実在、物理ページ、章を検証するが、LLMの意味解釈の正しさは自動保証しない。共通抽出と質問別のassessmentは両方残し、順位付けに使う質問別assessment内で、同一ソース名・用途・definition文字列に矛盾する分類や連結要否がある場合はconflictingとする。別のアウトカムを同じ用途に分類していても、直ちに矛盾とはみなさない。この検出は保守的な文字列照合で、言い換えを含むすべての意味的矛盾を検出するものではない。

## 表示順序と公式分類

比較行の`source_suitability`に判定、理由、全assessment、該当用途のassessment、矛盾、公式分類との差異を保存する。

| 状態 | 意味・順序 |
|---|---|
| matched | 該当用途に明示的な使用／使用予定の根拠があり、連結依存の記載なし。優先表示 |
| partial | 希望タイプが寄与するが、定義に別タイプの連結が必要 |
| possible | 希望タイプを本文記述から推定。明示的分類ではない |
| other_type | 該当用途に他タイプの明示的根拠。希望タイプの不使用の証明ではない |
| unknown | 未処理、記載不足、用途・使用状態が未確定 |
| conflicting | 矛盾を要確認。unknownと同じ優先度 |
| not_requested | 希望タイプ指定なし |

順位付けには質問別回答のsource_assessmentsを使う。共通抽出だけにある別アウトカムの分類を、質問への一致根拠へ流用しない。質問別の分類がなければunknown。共通抽出の全assessmentも別途保持して表示する。同じ優先度内は候補の順序を保持する。matchedもコード体系、利用可能変数、観察期間などの互換性を保証しない。希望タイプを変えただけなら、同じ質問とPDFの共通抽出・質問別回答を再利用して順位を計算する。

公式の`data_source_types`はStudyページのF8.7を別途返す。`data_source_types_source`と`data_source_types_checked_at`に由来・確認日時を残す。公式分類とPDF由来の明示的分類が重ならない場合は`catalogue_protocol_disjoint=true`とし、どちらも上書きしない。このフラグは意味の誤りを判定するものではなく、差異の確認用。

## 独立した上限

| 環境変数 | 既定値 | 対象 |
|---|---:|---|
| EMA_MAX_SCREENING_STUDIES | 5 | 一つの候補集合からPDF取得・全件解析へ進める最大研究数 |
| EMA_MAX_COMPARISON_STUDIES | 5 | 比較表に掲載できる最大研究数 |

それぞれ1〜1000の整数。`catalogue_status`と比較の`search`が実効値を返す。これはバッチあたり上限であり、長時間処理の成功を保証する値ではない。上限を増やすと通信・トークン・処理時間も増える。

例：一次判定12、比較5なら、候補12件まで全件PDF・JSONを保存し、全件の抽出と回答を完了する。比較候補が5件を超えたら`selection_status=needs_selection`となり、比較表を勝手に上位5件で埋めない。`screening_summary`と`eligible_study_ids`を提示し、ユーザーが選んだIDを`get_protocol_comparison(comparison_id, selected_study_ids=[...])`へ渡す。未選択研究もすべて保持する。候補集合自体を絞る場合は全検索式と追加条件で`compare_protocols`をやり直す。

全件処理が終わるまでは`screening_incomplete`、比較可能なら`ready`、厳密指定に確認済み一致がなければ`no_confirmed_matches`。`status=complete`は全行の処理完了で、比較対象の選択完了やすべての質問に回答できた意味ではない。

上限は比較開始時のスナップショット。設定変更後はサーバーを再起動し、新しい比較から適用する。このチェックアウトでは`.codex/config.toml`の`mcp_servers.ema-rwe.env`で変更できる。他の呼出元はMCPプロセスの環境変数で指定する。

```powershell
.venv/Scripts/ema-rwe compare "NVAF患者の定義" --query NVAF --query "non-valvular atrial fibrillation" --prefer-source-type claims --source-role cohort
.venv/Scripts/ema-rwe comparison cmp_返却されたID --study-id 123 --study-id 456
```

## キャッシュ更新と保全

DB schemaは3。既存DBに`analysis_history`を追加し、解析の無効化・置換時に旧JSONを退避する。古いschema／PDF／抽出設定の解析は最新として再利用しない。新スキーマで再抽出するため、初回は同じPDFの再読解が発生する。旧PDFと質問別回答も保持する。

Studies CSVにtype列がない場合は、取得済みの公式分類とその由来・確認日時を保持し、PDF解析も消さない。Data Sources CSVの継続登録、名称結合、ソースタイプごとのStudies CSV取得は不要。2026-09-12のData Sources結合調査レポートは調査履歴として残し、元CSVフォルダはユーザーの依頼で削除した。
