# 全候補の比較と保存

同一質問の全検索式を統合して数え、一次判定上限（EMA_MAX_SCREENING_STUDIES、既定5件）以内の研究を全件処理する。上限を超えたらデータソース種別と実施国をユーザーに尋ねて絞る（研究デザインで追加の絞り込み、Medicinal conditionはブロックで）。比較表の上限は独立したEMA_MAX_COMPARISON_STUDIES（既定5件）。タイプ判定と優先順位は[用途別分類](source-types.md)を参照。

## 検索と絞込

手順の正本は[呼出元の手順](mcp-workflow.md)（MCPリソース`ema-rwe://docs/mcp-workflow`でも読める）。ここでは比較に関わる部分をまとめる。

1. `plan_study_search` と呼出元の推論で、英語の名称・言い換え・略語・ICD-10/ATC等の検索候補を作る（[臨床概念の展開](clinical-search.md)）。未知の日本語薬剤名は英訳し、医薬品辞書が期限切れなら更新する。
2. `compare_protocols` に、概念ごとのブロック（`blocks: [{role, queries, category_terms}]`）を渡す。ブロック内の語はOR、ブロック間はAND。1概念だけの質問は、ブロックの代わりに`queries`・`role`・`category_terms`を使ってもよい（`blocks`と併用しない）。検索語は1ブロック20語、全体で40語まで。新しい検索語を試す際も既存の検索語を含める。
   - `queries`：固有の名称と言い換え。各語は1つの語句・医薬品名として語全体で照合する。医薬品の語はサーバーがカタログの「(ATCコード) 名称」とEMA医薬品辞書で名前へ展開し、加えた語を`medicine_expansion`に返す。
   - `category_terms`：カタログの記録が固有名の代わりに使う上位の語（ICD-10のブロック名、MACEなどの複合アウトカム）。カテゴリー語だけで一致した研究は後ろに並ぶ。
   - `role`（outcome／condition／exposure／any）は絞り込みではなく順位付けに使う。全列を検索し、役割の列（題名と、Outcomes、Medicinal condition、医薬品欄など役割ごとの列）で一致した研究を前に並べる。Outcomes欄が空の記録があるため。
3. 候補は削らずに並べる（`ranking.order_key`）。①公開プロトコルが無いと確認された研究、テキスト層の無いPDF（画像だけ）の研究は最後、②固有の語で一致したブロックが多い研究、③役割の列で一致したブロックが多い研究、④CSVにプロトコルの所在がある研究または取得でプロトコルが見つかった研究、⑤二次利用データ（カタログの種別タグあり）の研究を前に、調査（題名にsurvey）・横断研究を後ろに、⑥検索語ごとのBM25順位の統合値（RRF、k=20）。各候補は`rank_features`、`matched_terms`、`matched_term_sources`、補完した医薬品`observed_exposures`を持つ。
4. `needs_narrowing` なら、`total_matches`、`facets`（国・種別・デザイン・Medicinal condition）、`unknown_metadata_counts` を示して、データソース種別（claims／ehr／registry／others）と実施国の両方をユーザーに尋ね、`filters`に渡して同じブロックで再実行する。`filters.study_designs`でさらに絞ってもよい。`facets.conditions`（Medicinal condition）はフィルタではないので、それで絞るときは`role=condition`のブロックとしてAND で加える。候補が`EMA_MAX_LISTED_CANDIDATES`（既定50）以内なら`candidates`一覧が返る（`candidates_listed=true`）。ユーザーが一覧から選んだ`study_ids`（一次判定上限以内）を同じブロックで渡すと、その研究だけを一次判定に進める。選ぶ前に`check_protocols=N`（0〜20）で上位N件のStudy documentsを確かめ、プロトコルの無い研究を最後に回せる（PDFは取得しない）。別の検索語や表示limitだけで件数を小さく見せたり、任意の5件を選んだりしない。
5. 0件のときは検索を広げずに、類縁概念とその件数を`analogous_fallback`で示す（複数ブロックでは`not_available_for_blocks`。0件の概念だけで実行し直す）。ユーザーが選んだら`match_scope=analogous`で再実行し（`blocks`とは併用できないので、1つのブロックの語と役割を`queries`・`role`・`category_terms`に移す）、結果は類縁概念の研究として示す。0件の医薬品で呼出元が第5レベルのATCコードを渡していれば、同じクラスとカタログ上の所属薬も類縁概念になる。フィルタで除外した不明メタデータがないかも報告する。

```json
{
  "question": "日本のclaimsデータでNVAF患者をどう定義していますか？診断コード・回数・期間・除外条件を比較してください。",
  "blocks": [
    {"role": "condition",
     "queries": ["NVAF", "non-valvular atrial fibrillation", "nonvalvular atrial fibrillation"],
     "category_terms": ["atrial fibrillation and flutter"]}
  ],
  "darwin_only": false,
  "filters": {
    "countries": ["Japan"],
    "study_designs": ["cohort", "case-control"]
  },
  "source_preference": {"types": ["claims"], "role": "cohort", "mode": "prefer"}
}
```

国はカタログの英語表記を指定する。米国/US/USA、英国/UKの別表記や、研究数の多い国の日本語名（日本、ドイツなど）も受け付ける。同一フィールド内はOR、フィールド間はAND。ソース種別はclaims、ehr、registry、others（othersは種別限定exportのいずれにも含まれない研究）。研究デザインはcohort、case-control、cross-sectional、ecological、self-controlled。

希望タイプはsource_preferenceへ渡す。PDFの質問別探索から根拠付きで判定し、公式分類は別に保持する。types内はOR。mode=preferは他タイプも残し、onlyは明示的な限定要求で使用する。filters.data_source_typesはカタログの種別タグによる候補の絞り込みで、source_preferenceとは独立。研究デザインは専用フィールド、欠落時のみ保存済みの抽出事実を使う。一般的なNon-interventionalという研究種別とcohort等の研究デザインは別項目。

候補数は登録済みのローカルメタデータと保存済み解析による検索の一致数（全ブロックの条件を満たす研究の重複除去後の数）。複数語の検索語は、同じ列の中で語が近くに現れた場合だけ一致する（FTS5 NEAR。距離は max(3, 語数−1)。検索語そのものは、元の文字列の機能語も数えて距離を決める）。表示上限より前に数える。サイト全体の検索結果総数ではなく、未取得PDFの内容まで検索した結果でもない。プロトコルの有無や質問への適合性は後続処理で確認するため、一次判定上限を超えたら候補段階で絞込を求める。種別タグは`data/imports/source_type/`の種別限定exportから付与し、タグのない研究はothersとして絞り込める。未登録研究を調べるには公式CSVを追加取込するか、既知のStudy IDで `get_study` を実行して検索対象へ登録する。Drupal node IDをStudy IDとして使わない。

## 全件処理

一次判定上限以内のとき `compare_protocols` は全研究についてStudy documentsから選んだ最新PDFを取得・保持し、各研究の下書きJSONを作る。最新版は既存のTTL・版選択規則に従う。PDF内容を差し替えた場合は異なるIDで保持する。

戻り値の `pending_tools` を全件実行する。

- `analyze_protocol`：共通抽出。`extracting` ならサーバー側抽出中なので同じ呼出を繰り返して結果を受け取る。`needs_client_extraction` なら `next_offset` がなくなるまで全バッチを読み、バッチごとに `cache_protocol_analysis(batch_offset=offset)` で途中保存し、最後に `coverage_complete=true` を渡す。
- `research_protocol`：質問別の追加探索。`needs_client_exploration` なら `get_protocol_outline`、`search_protocol_text`、`read_protocol_text` で方法・定義・隣接章・コード付録を確認し、`cache_protocol_answer` に保存する。
- 最後に `get_protocol_comparison(comparison_id)`：保存済み解析と質問別回答を全件集約してJSON/Markdownを更新する。未処理が残れば繰り返す。

内部LLM設定時も上記のツール順序は同じ。各解析・探索ツールが内部APIを呼んで検証・保存する。比較ツール1回が内部で全件分のLLM処理を連続実行する設計ではない。APIの長い探索がクライアントのツールタイムアウトを超える場合は上限・タイムアウト設定の調整が必要。

比較を始める前に読み直しを必要とする場合は `get_study(refresh=true)` や `get_protocol(refresh=true)` を使う。通常はTTL内のキャッシュを再利用する。

## 出力

比較表（`comparison.md`）は研究を列にし、先頭行は「一致の根拠」（依頼概念か類縁概念か、一致した語）。行は、国・Data source type・希望タイプへの適合性・PDF由来のタイプ・PDF記載データソース・Study design・対象集団・**コホート定義（組入・除外・インデックス日・ベースライン・追跡）**・**設計図（ページ・時間窓）**・疾患定義・問い合わせへの回答・根拠ページ・未記載事項。コホート定義と設計図は抽出スキーマv0.3の`cohort`ブロックから作られ、図そのものは読まず本文の時間窓記述を載せる。

```text
<EMA_PROTOCOL_DIR>/pdf_<Study ID>_<SHA256>.pdf
<DBの親フォルダ>/comparisons/cmp_<ID>/
  study_<Study ID>.json   # 全候補について1ファイルずつ
  comparison.json        # 質問・検索展開・条件・全研究・処理状態
  comparison.md          # 比較上限以内の研究を列にした表と全一次判定一覧
```

`rows` にはStudy ID、国、Data source typeと取得状態、PDF記載データソースと使用状態、PDF ID・ローカルパス、共通抽出、質問別回答、JSONパスを含める。抽出と回答の各事実には物理ページ・章・原文引用がある。カタログ由来の名称は `study.catalogue_data_sources` に区別して残す。未解析を空欄だけで表現せず `not_analyzed` と明示する。回答できない項目は `missing_information` に調査範囲・理由を残す。

全研究のPDF・一致するfingerprintの抽出・質問別回答がそろうと `status=complete`。これは処理完了を表し、全情報が見つかったことやLLM解釈の正確性を保証しない。

取得失敗、プロトコル未登録、対象外研究も研究行とJSONにエラーを残し、後続研究の取得を続ける。1件でも未完了なら比較全体は `incomplete`。PDF取得途中で中断した場合も下書きを保持する。取得エラーを解消して比較を作り直す場合は同じ検索条件で `compare_protocols` を再実行する。

比較は選択したPDFと抽出設定のスナップショット。途中でPDFまたは抽出設定が変わり、異なるfingerprintの解析が保存された場合は `COMPARISON_SOURCE_CHANGED` を返し、別版を混ぜない。新しい比較を作り直す。`get_protocol_comparison` は通信せずローカルPDFのハッシュとDBを確認するだけなので、サイトでの最新版を再確認した意味にはならない。

CLIは同じCoreを使う。

```bash
uv run ema-rwe compare "NVAF患者の定義" --query "NVAF" --query "non-valvular atrial fibrillation" --role condition --prefer-source-type claims --source-role cohort --country Japan
uv run ema-rwe comparison "cmp_<戻り値のID>"
```

比較表には根拠ページとPDFリンクを含めてユーザーに提示し、JSONへのリンクも添える。一部の研究だけを代表として回答しない。

## 一次判定数と比較表の上限が異なる場合

一次判定上限12、比較上限5などを設定できる。上限は開始時のsearchへ保存し、既存比較の途中では変えない。全件解析が完了しても比較候補が表示上限を超える場合は、selection_status=needs_selectionとなり、全screening_summaryとeligible_study_idsからユーザーに追加条件または選択を求める。選択後にget_protocol_comparison(comparison_id, selected_study_ids=[...])を呼ぶ。自動的な上位N件選択はしない。非掲載の研究もrows、PDF、JSON、一次判定一覧に残す。

status=completeは処理完了であり、表示対象の選択完了とは別。selection_statusはscreening_incomplete／needs_selection／ready／no_confirmed_matches。厳密指定に一致しない・判定不能・取得失敗の研究も明示する。
