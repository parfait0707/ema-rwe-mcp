# ema-rwe MCP の配布方式の比較

## 現状（clone 前提になっている理由）

`.mcp.json` の 3 サーバーは配布方式が異なる。

| サーバー | 起動コマンド | 利用者に必要なもの |
|---|---|---|
| context7 | `npx -y @upstash/context7-mcp` | Node（npm registry から都度取得） |
| serena | `uv tool run --from git+https://github.com/oraios/serena serena ...` | uv（公開 Git リポから都度取得） |
| ema-rwe | `uv run --directory /workspace ema-rwe-mcp` | **このリポの clone + uv sync** |

ema-rwe が clone を要求する原因は 2 つある。

1. **コードの取得元がローカル checkout**。`pyproject.toml` は hatchling で wheel を作れる
   （`dist/ema_rwe_mcp-0.6.0-py3-none-any.whl` 生成済み）が、どこにも公開していない。
   リポジトリは private なので serena 方式（`git+https://`）はそのままでは使えない。
2. **データが wheel に入っていない**。`[tool.hatch.build.targets.wheel] packages = ["src/ema_rwe"]`
   のため、`data/ema.sqlite3`（42 MB）、`data/ema-medicines.json`（6.5 MB）、
   `data/terminology.json` は wheel 外。`config.py` の `default_db_path()` は checkout 外では
   `user_data_path("ema-rwe-mcp")/ema.sqlite3` を指すが、そこへ DB を置く手段がない
   （`import-all` の元 CSV も untracked）。wheel だけ入れても空 DB で起動する。

したがって「コードの配布経路」と「データの配布経路」を別々に決める必要がある。

## コードの配布経路

### A. PyPI 公開 + `uvx`（最も簡便）

```json
"ema-rwe": {"command": "uvx", "args": ["ema-rwe-mcp"]}
```

- 利用者は uv だけあればよい。clone・venv・環境変数不要。context7 の Python 版に相当。
- 必要作業: PyPI アカウント、`uv build && uv publish`（GitHub Actions の Trusted Publishing が定石）。
- 制約: パッケージ名 `ema-rwe-mcp` が空いているか要確認。コードが公開になる。

### B. GitHub から直接（serena 方式）

```json
"ema-rwe": {"command": "uvx", "args": ["--from", "git+https://github.com/parfait0707/rwd-catalogue-mcp", "ema-rwe-mcp"]}
```

- 公開作業ゼロ。タグ指定（`@v0.6.0`）で版固定できる。
- 制約: リポが private のため利用者に GitHub 認証（`gh auth` の credential helper か SSH URL）が要る。
  リポを public にすれば serena と同じ手軽さになる。
- 初回 clone が重い（`data/` 約 50 MB + 履歴）。データを LFS や Release に逃がすと軽くなる。

### C. GitHub Release に wheel を添付

```json
"ema-rwe": {"command": "uvx", "args": ["--from", "https://github.com/.../releases/download/v0.6.0/ema_rwe_mcp-0.6.0-py3-none-any.whl", "ema-rwe-mcp"]}
```

- PyPI 不要で版固定できる。private リポの Release asset は認証が要る点は B と同じ。

### D. Docker イメージ

```json
"ema-rwe": {"command": "docker", "args": ["run", "-i", "--rm", "-v", "ema-rwe-data:/data", "ghcr.io/parfait0707/ema-rwe-mcp"]}
```

- Python/uv 不要でデータも同梱できる。利用者に Docker が要り、PDF 保存先やキャッシュを
  volume で外出しする設計が必要。研究者個人の PC 向けには A/B より重い。

### E. リモート MCP（HTTP/SSE でホスト）

- 利用者はインストール不要で URL 登録のみ。ただし本 MCP は PDF をローカル保存し
  `data/protocols/` を蓄積する設計なので、共有サーバー化はマルチテナントの設計変更を伴う。
  今回の要件（簡便な配布）に対して過大。

## データの配布経路（A〜C いずれでも必要）

### D1. wheel に同梱

- `pyproject.toml` に `[tool.hatch.build.targets.wheel.force-include]` で `data/ema.sqlite3` 等を
  `ema_rwe/data/` に入れ、`default_db_path()` を「同梱 DB をユーザーデータ dir へ初回コピー」に
  変える（DB は書き込まれるので site-packages 直下で開かない）。
- wheel が約 50 MB になる。PyPI の 1 ファイル上限 100 MB 内。sqlite は zip 圧縮で数分の 1 になる。
- 最も簡便。カタログ更新のたびにパッケージ版を上げる運用になる。

### D2. 初回起動時に GitHub Release から取得

- `ema-rwe fetch-catalogue` を追加し、Release asset の `ema.sqlite3` + SHA256 をユーザーデータ dir へ
  ダウンロード。`catalogue_status` が未取得を返したら呼出元がこれを実行する。
- コードとデータの版を独立に更新できる。private リポなら認証が要る。

### D3. 利用者自身が公式 CSV を export して `import_catalogue_csv`

- 既存機能のみで済むが、種別タグ付き export を 4 回集める手間があり「簡便」から遠い。
  D1/D2 の補助手段として残す。

## 推奨

- **コード: A（PyPI + uvx）**。公開できない場合は **リポを public にして B**。
- **データ: D1（wheel 同梱）**。理由: 追加コードが `force-include` 数行と初回コピー処理だけで、
  利用者の手順が `uvx ema-rwe-mcp` の 1 行になる。更新頻度が上がって wheel サイズが問題化したら D2 へ。

利用者側の最終形:

```json
{"mcpServers": {"ema-rwe": {"command": "uvx", "args": ["ema-rwe-mcp"]}}}
```

## 実装が必要な変更（採用時）

1. `pyproject.toml`: `force-include` で `data/{ema.sqlite3,ema-medicines.json,terminology.json,terminology_decisions.json}` を同梱
2. `config.py`: `default_db_path()` / `default_terminology_path()` / `drugs.py` の既定を「checkout → 同梱資産をユーザーデータ dir へ初回コピー → そのパス」に変更
3. GitHub Actions で tag push 時に `uv build` → PyPI Trusted Publishing
4. README の「セットアップ」に uvx 1 行の手順を追加、`.mcp.json` は開発用（checkout 直起動）のまま維持
