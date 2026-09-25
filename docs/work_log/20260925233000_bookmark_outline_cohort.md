# しおりによる読取範囲の絞り込みと抽出スキーマ v0.3（cohort）

## 指示
1. PR #12（応答圧縮）をマージ。
2. PDF のしおりを使って探索範囲を絞る（しおりなしは従来どおり）。提案 3 点（しおり優先の章読み、参照付録の追跡、質問別探索の章名優先）を採用。
3. スキーマ v0.3 に cohort ブロックを追加。画像対応は不要。図のページ番号と本文の時間窓記述は含める。

## 実装（ブランチ feature/bookmark-outline-cohort）
- `pdf.py`: `Page.chapter/chapter_top`、`apply_bookmarks`、`reading_order`、章名一致の加点、検証・除去の再帰化
- `domain.py`: `Cohort`, `DesignSchema`, `Extraction` v0.3
- `llm.py`: プロンプトに cohort の指示、`merge_into`（再帰統合）、`drop_invalid_evidence` の再帰
- `service.py` / `exploration.py`: `reading_order` を使用、fingerprint の schema 0.3
- `comparison.py`: 比較表に「コホート定義」「設計図（ページ・時間窓）」列
- テスト 6 件追加（`tests/test_bookmarks_cohort.py`）。全 215 件合格
- `.env.example`（ユーザー編集分: LLM_REASONING_EFFORT / LLM_MAX_TOKENS ほか）を同じ PR に含める

## 実測
`docs/validation.md` の 2026-09-25 節を参照（19786 の読取量 -77%、cohort ブロックが実データで充足）。

## 懸念・未実施
- しおりのない PDF（手元 17 本中 7 本）は従来どおり全読。
- 付録の参照検出は「Annex/Appendix + 番号（ローマ数字含む）」と題名のコードリスト語に依存。表番号だけで参照する付録は読まれない可能性がある。
