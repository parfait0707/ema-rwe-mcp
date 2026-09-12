# 全候補の比較と保存（v0.4）

2026-09-12の追加要望に基づき、同一質問に複数研究が該当する場合は全件を比較する。最大5件。ちょうど5件は処理し、6件以上なら国・ソース種別・研究デザインの追加条件をユーザーに尋ねる。

## 検索と絞込

1. `plan_study_search` と呼出元の推論で英語の同義語・略語・ICD-10/ATC等の検索候補を作る。未知の日本語薬剤名は英訳し、医薬品辞書が期限切れなら更新する。
2. `compare_protocols(question=..., queries=[全検索語], synonyms=[...], codes=[...], darwin_only=false)` に渡す。各queryの検索結果をStudy IDで重複除去する。新しい検索語を試す際も既存の検索語を含める。
3. `needs_narrowing` なら、`total_matches`、`facets`、`unknown_metadata_counts` を使って追加条件を尋ねる。別queryや表示limitだけで件数を小さく見せたり、任意の5件を選んだりしない。
4. 条件を加えた同じ検索語集合で再実行する。0件なら登録範囲・英訳・フィルタを見直す。フィルタで除外した不明メタデータがないかも報告する。

```json
{
  "question": "日本のclaimsデータでNVAF患者をどう定義していますか？診断コード・回数・期間・除外条件を比較してください。",
  "queries": ["NVAF", "non-valvular atrial fibrillation", "nonvalvular atrial fibrillation"],
  "darwin_only": false,
  "filters": {
    "countries": ["Japan"],
    "data_source_types": ["claims"],
    "study_designs": ["cohort", "case-control"]
  }
}
```

国はカタログの英語表記を指定する。日本/JP、米国/US/USA、英国/UKの別表記も受け付ける。同一フィールド内はOR、フィールド間はAND。ソース種別はclaims、registry、ehr、drug_dispensing_prescription。研究デザインはcohort、case-control、cross-sectional、ecological、self-controlled。

ソース種別はカタログの専用フィールドに基づき、研究タイトルに出てきただけの語で判定しない。研究デザインは専用フィールド、欠落時のみ保存済みの抽出事実を使う。一般的なNon-interventionalという研究種別とcohort等の研究デザインは別項目。

候補数は登録済みのローカルメタデータと保存済み解析による検索の一致数。検索結果表示の5件上限より前に数える。サイト全体の検索結果総数ではなく、未取得PDFの内容まで検索した結果でもない。プロトコルの有無や質問への適合性は後続処理で確認するため、6件以上ならこの候補段階で保守的に絞込を求める。未登録研究を調べるには公式CSVを追加取込するか、既知のStudy IDで `get_study` を実行して検索対象へ登録する。Drupal node IDをStudy IDとして使わない。

## 全件処理

1～5件のとき `compare_protocols` は全研究についてStudy documentsから選んだ最新PDFを取得・保持し、各研究の下書きJSONを作る。最新版は既存のTTL・版選択規則に従う。PDF内容を差し替えた場合は異なるIDで保持する。

戻り値の `pending_tools` を全件実行する。

- `analyze_protocol`：共通抽出。`needs_client_extraction` なら `next_offset` がなくなるまで全バッチを読み、`cache_protocol_analysis` に根拠付きJSONと `coverage_complete=true` を渡す。
- `research_protocol`：質問別の追加探索。`needs_client_exploration` なら `get_protocol_outline`、`search_protocol_text`、`read_protocol_text` で方法・定義・隣接章・コード付録を確認し、`cache_protocol_answer` に保存する。
- 最後に `get_protocol_comparison(comparison_id)`：保存済み解析と質問別回答を全件集約してJSON/Markdownを更新する。未処理が残れば繰り返す。

内部LLM設定時も上記のツール順序は同じ。各解析・探索ツールが内部APIを呼んで検証・保存する。比較ツール1回が内部で5件分のLLM処理を連続実行する設計ではない。APIの長い探索がクライアントのツールタイムアウトを超える場合は上限・タイムアウト設定の調整が必要。

比較を始める前に読み直しを必要とする場合は `get_study(refresh=true)` や `get_protocol(refresh=true)` を使う。通常はTTL内のキャッシュを再利用する。

## 出力

```text
<EMA_PROTOCOL_DIR>/pdf_<Study ID>_<SHA256>.pdf
<DBの親フォルダ>/comparisons/cmp_<ID>/
  study_<Study ID>.json   # 全候補について1ファイルずつ
  comparison.json        # 質問・検索展開・条件・全研究・処理状態
  comparison.md          # 最大5研究を列にした比較表
```

`rows` にはStudy ID、国、Data source typeと取得状態、PDF記載データソースと使用状態、PDF ID・ローカルパス、共通抽出、質問別回答、JSONパスを含める。抽出と回答の各事実には物理ページ・章・原文引用がある。カタログ由来の名称は `study.catalogue_data_sources` に区別して残す。未解析を空欄だけで表現せず `not_analyzed` と明示する。回答できない項目は `missing_information` に調査範囲・理由を残す。

全研究のPDF・一致するfingerprintの抽出・質問別回答がそろうと `status=complete`。これは処理完了を表し、全情報が見つかったことやLLM解釈の正確性を保証しない。

取得失敗、プロトコル未登録、対象外研究も研究行とJSONにエラーを残し、後続研究の取得を続ける。1件でも未完了なら比較全体は `incomplete`。PDF取得途中で中断した場合も下書きを保持する。取得エラーを解消して比較を作り直す場合は同じ検索条件で `compare_protocols` を再実行する。

比較は選択したPDFと抽出設定のスナップショット。途中でPDFまたは抽出設定が変わり、異なるfingerprintの解析が保存された場合は `COMPARISON_SOURCE_CHANGED` を返し、別版を混ぜない。新しい比較を作り直す。`get_protocol_comparison` は通信せずローカルPDFのハッシュとDBを確認するだけなので、サイトでの最新版を再確認した意味にはならない。

CLIは同じCoreを使う。

```powershell
.venv/Scripts/ema-rwe compare "NVAF患者の定義" --query "NVAF" --query "non-valvular atrial fibrillation" --source-type claims --country Japan
.venv/Scripts/ema-rwe comparison "cmp_<戻り値のID>"
```

比較表には根拠ページとPDFリンクを含めてユーザーに提示し、JSONへのリンクも添える。一部の研究だけを代表として回答しない。
