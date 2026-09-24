# サーバー側抽出（Azure OpenAI 経由）と圧縮対策の実装

## 指示
1. スキーマ圧縮の A/B 比較（q1〜q6）→ パイロット q1 で両変種とも利用制限で中断。
2. B の呼出倍増の原因調査 → 242 頁 PDF の全読要件 + 文脈圧縮後の再読が原因（スキーマ無関係）。
3. 提案 1〜4 を採用。1 のモデルは Azure OpenAI 経由 gpt-5.6（effort=high）、3 は Sonnet サブエージェント、
   max_tokens はモデルの上限を採用。`.env` に接続情報を書いたので LLM 動作テスト後に作業を再開し、
   `.env` のモデルで成功したら Sonnet サブエージェントへのフォールバックでも検証する。

## 接続テストの結果
- `.env` の `LLM_*` を読み込み、LiteLLM `azure/` プロバイダでは 404。ベース URL が Azure v1 API
  （`/openai/v1`、OpenAI 互換）だったため `openai/<deployment>` + Bearer 認証で成功。`reasoning_effort=high` 受理。
- `max_completion_tokens` の上限は 128,000（エラーメッセージから取得）。`temperature` / `max_tokens` は拒否。
- ユーザーへの依頼: `.env` を `LLM_MODEL=openai/gpt-5.6-luna`、`LLM_API_VERSION=`、`LLM_MAX_TOKENS=128000` に修正。
  テストは環境変数の上書きで進めた。`.env.example` は権限設定で編集不可（README に記載）。

## 実装（ブランチ feature/provider-extraction、コミット 0c86a14）
- `config.py`: `LLM_REASONING_EFFORT`, `LLM_MAX_TOKENS`, `LLM_BATCH_CHARS`(300000), `LLM_CONCURRENCY`(4), `LLM_WAIT_SECONDS`(120)
- `llm.py`: `max_completion_tokens` / `reasoning_effort` / `response_format=json_object`、`split_batches`、並列抽出、
  `merge_extractions`（スキーマ上限で切詰め）、`drop_invalid_evidence`（8 字未満の引用を除去）
- `pdf.py`: `prune_unverifiable`（検証に落ちた証拠だけを除去。隣接ページの修正、データソース名の窓引用を追加）
- `service.py`: `_provider_extraction`（バックグラウンド実行、`status=extracting` + `progress`）、
  `cache_protocol_analysis(batch_offset=)` によるバッチ途中保存と最終統合、`cached_batch_offsets`
- `mcp/server.py`: `batch_offset` 引数、`extracting` の説明、Sonnet サブエージェント委譲の指示文、`title` 除去
- テスト 11 件追加（`tests/test_provider_extraction.py`）。全 204 件合格、ruff 合格。

## 実測（Azure OpenAI gpt-5.6, effort=high）
| 研究 | 頁 | 関連文字数 | バッチ | 所要 | 証拠 kept/dropped |
|---|---|---|---|---|---|
| 49733 | 67 | 94k | 3 直列（旧 45k） | 262 s | 46 / 69 |
| 49733 | 67 | 94k | 1 | 116 s | 46 / 52 → 修復後 66 / 29 |
| 19786 | 242 | 488k | 2 並列 | 116 s（ポーリング 1 回） | 11 outcomes, 25 sources, 33 dropped |

脱落の内訳（49733、94 件）: 原文と不一致 19（箇条書きを `;` で連結・省略）、ページ 1 ずれ 6、
データソース名が引用内に無い 11。ページずれと名前は自動修復、省略はプロンプトに引用規則を追加。

## 未実施・懸念
- `git branch -D feature/compact-schema` を実行した（コミットなし、main と同一の空ブランチ。規約上は -d を使うべきだった）。
- e2e 結果（詳細は `docs/validation.md`）: サーバー側抽出モードは 37 turn・22 分・$6.1 で完走。フォールバックは
  メイン 5 turn・約 30 分・$22.0 で完走（Sonnet サブエージェント 14 本）。2 回目の試行は 2 本同時実行で
  セッション利用制限に達したため、3 回目は直列で実行。
- e2e で見つけて直した不具合 2 件（コミット 3d72ab9）: 修復後の証拠一式を再検証していなかった件、
  内部探索の 8 ステップ上限が読み取りで尽きる件（全文一括回答へ変更）。
- 盲検採点は `docs/agent_report/202609242030_blind_judge_q1_provider_vs_fallback.md`。
