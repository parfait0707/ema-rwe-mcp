# `.mcpb`（MCP Bundle）配布の可否調査

- 日付：2026-10-09（日本時間）
- 対象：v0.5.5／main `ee49e299849c04f80e0307a43f5135a6b52bbd04`
- 調査時の実装変更はなし。試作はセッションのスクラッチ領域だけで行った。
- 判断：**見送り**。`.mcpb` を導入できるのは Claude Desktop だけであり、そのためだけに配布経路を増やす必要はないとユーザーが判断した。

## 調査目的

これまでの git 経由（`uvx --from git+...`）の利用に加えて、`.mcpb` ファイルとして配布できるかを検討する。
MCP の最新仕様を確認し、このリポジトリの stdio MCP サーバを `.mcpb` にできるかを判定する。

## 前提と制約

- サーバは Python 3.12 以上で、`mcp>=1.12,<2`（lock は 1.30.0）を使う。
  依存にはコンパイル済みの pydantic と PyMuPDF が含まれる。
- wheel は `data/ema.sqlite3`（約 44MB）、`data/ema-medicines.json`（約 6.5MB）、`docs/mcp-workflow.md` を同梱する。
- 実行環境は Linux のコンテナである。Claude Desktop（macOS／Windows）での実機確認はできていない。

## 調査方法と参照元

参照日はいずれも 2026-10-09。

- MCP 仕様 2026-07-28 の変更一覧：<https://modelcontextprotocol.io/specification/2026-07-28/changelog>
- MCPB の manifest 仕様：<https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md>
- MCPB の README と例 `examples/hello-world-uv`：<https://github.com/modelcontextprotocol/mcpb>
- Claude の MCPB 解説：<https://claude.com/docs/connectors/building/mcpb>
- MCP Python SDK v2.0.0：<https://pypi.org/project/mcp/2.0.0/>
- npm の `@anthropic-ai/mcpb@2.1.2` を取得し、同梱の JSON Schema（v0.1〜v0.4 と latest）を直接読んだ。
- `mcpb validate` と `mcpb pack` で試作バンドルを作成した。
  展開したものを `uv 0.12.9` で起動し、stdio で `initialize`・`tools/list`・`tools/call catalogue_status` を送った。

## 主な発見

### MCP 本体の仕様（2026-07-28）

- 初回の handshake（`initialize`）とプロトコル層のセッションが廃止された。
  各リクエストは `_meta` に版とクライアント能力を持つ。サーバは `server/discover` の実装が必須になった。
- Roots・Sampling・Logging は Deprecated になった（最短 12 か月の猶予期間がある）。
  本サーバの `mcp/server.py` はこの 3 機能を使っていない。
- Python SDK v2.0.0 は 2026-07-28 版に対応し、2025 年版のクライアントにも同じサーバで応答する。
  本リポジトリは `mcp<2` で 2025 年版として動く。
  2026 版への移行は `.mcpb` 対応とは独立した作業である。

### MCPB の仕様

- 安定版の manifest は 0.3 である（CLI 同梱の `latest` schema が `manifest_version: "0.3"` を指す）。
  `server.type` は `python`・`node`・`binary` に限られる。
- `server.type: "uv"` は manifest 0.4 で追加された。
  バンドルに `pyproject.toml` を入れ、依存は導入先のホストが uv で用意する。
  `server/lib` と `server/venv` は同梱してはならない。
- 従来の `python` 方式では、pydantic などのコンパイル済み依存をプラットフォームをまたいで同梱できない。
  そのため本サーバで現実的なのは uv 方式だけである。
- Claude の公式解説では Node.js を強く推奨している。
  また Anthropic のディレクトリは MCPB の掲載受付を終了しており、配布は利用者による手動導入になる。
- `.mcpb` を導入できるクライアントは Claude Desktop（macOS・Windows）だけである。
  Claude Code と Codex は従来どおり stdio のコマンド登録で使う。

### 試作の結果

manifest 0.4・uv 方式で試作した。
起動コマンドは `uv run --frozen --no-dev --directory ${__dirname} ema-rwe-mcp` とした。

- `mcpb validate` は通った。`mcpb pack` の結果は圧縮後 17.6MB、展開後 50.9MB、32 ファイルだった。
- 起動すると 18 ツールを返し、`catalogue_status` は `status: current` を返した。
- 既定の起動では、uv が本パッケージを editable で導入する。
  このとき `config.py` の `REPO_ROOT`（`parents[2]`）がバンドルの直下を指す。
  そこには `pyproject.toml` があるため、サーバはバンドルを開発用チェックアウトと判定する。
  その結果、データの保存先が `<拡張機能フォルダ>/data` になった。
  拡張機能を更新すると、キャッシュした解析や PDF が失われうる。
- 起動引数に `--no-editable` を加えると、パッケージは `.venv/.../site-packages` に入る。
  保存先は `user_data_path("ema-rwe-mcp")` になり、同梱 DB の複製と `protocols/` もそこに作られた。
  wheel を `uvx` で使う場合と同じ経路である。
- `.python-version` を同梱しないと、uv は Python 3.14 を取得した。配布物には同梱して 3.12 に固定する必要がある。
- uv 方式でも、初回起動時には Python 本体と依存をネットワークから取得する。
  公式解説にある「オフラインで動く」は 2 回目以降の起動にしか当てはまらない。

## 選択肢と比較

| 経路 | 対象クライアント | 導入の手間 | 更新 |
|---|---|---|---|
| GitHub＋`uvx`（現行） | Claude Code・Codex・Claude Desktop ほか | uv と設定ファイルの編集が必要 | タグの付け替えか `@latest` |
| `.mcpb`（uv 方式） | Claude Desktop のみ | ダブルクリックで導入できる | 新しい `.mcpb` を利用者が入れ直す |
| PyPI＋`uvx` | 現行と同じ | git が不要 | `uvx ...@latest` で最新版を取得できる |

`.mcpb` の利点は Claude Desktop 利用者の導入の容易さに限られる。
現行の経路を置き換えるものではなく、配布経路を一つ増やすことになる。

## 結論

- 技術的には配布できる。必要な作業は次の 5 点である。
  - manifest 0.4（uv 方式、起動引数 `--frozen --no-dev --no-editable`）を用意する。
  - `.python-version` と `uv.lock` を同梱する。
  - `.mcpbignore` で `data/protocols` などを除外する。
  - `user_config` を環境変数へ受け渡す。
  - manifest と `pyproject.toml` の version を揃える確認をリリース手順に加える。
- 同梱 DB を更新した版を出せば、既存の `refresh_from_bundle` がカタログの表だけを差し替え、利用者がキャッシュした解析は残る。
  月次 DB の更新を `.mcpb` の再配布で届ける運用は、今の実装で成り立つ。
- ただし対応クライアントが Claude Desktop だけである。そのため今回は実装しない（ユーザー判断）。

## 未解決事項

- 実機の Claude Desktop（macOS・Windows）で、uv 方式のバンドルが導入でき、起動できるかは未確認である。
  Windows での PyMuPDF の動作も同様に未確認である。
- `user_config` の任意の数値項目が未入力のときに空文字が渡ると、`Settings` の `int("")` が失敗する可能性がある。
  この点も実機での挙動は未確認である。
- 2026-07-28 版の仕様への移行（`mcp` SDK v2）は別の課題として残る。
- 再検討のきっかけになりうる変化は次の 2 つである。
  - Claude Code や Codex など、他のクライアントが `.mcpb` に対応した場合。
  - 利用者の大半が Claude Desktop になった場合。
