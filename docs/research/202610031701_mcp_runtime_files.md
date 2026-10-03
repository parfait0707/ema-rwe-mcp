# MCP の動作に必要なファイルと、リポジトリに置くファイル

2026-10-03、v0.5.3 時点の調査記録。配布方式を決めた[配布方式の比較](202609160750_mcp_distribution.md)（2026-09-16）の後続にあたる。

## 調査目的

「エンドユーザーが MCP を使うのに不要なファイルが、リポジトリに push されていないか」という問いに答える。あわせて、`AGENTS.md`、`docs/`、`scripts/`、`tests/` が MCP の動作に必要かを確かめる。

## 前提と制約

- 利用者は `.mcp.json.sample` のとおり、`uvx --from git+https://github.com/parfait0707/ema-rwe-mcp@v<版> ema-rwe-mcp` で起動する。PyPI には公開していない。
- ビルドは hatchling で行う。`pyproject.toml` の `[tool.hatch.build.targets.wheel]` は `packages = ["src/ema_rwe"]` で、`force-include` で次の3つを wheel に加えている。
  - `data/ema.sqlite3`
  - `data/ema-medicines.json`
  - `docs/mcp-workflow.md`
- MCP サーバーは stdio トランスポートで動く。MCP ホスト（Claude Code、Codex など）がサーバーをローカルの子プロセスとして起動し、標準入出力で JSON-RPC をやりとりする（[MCP Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture)）。

## 調査方法・参照元

- `uv build` で作った wheel と sdist の中身を `unzip -l`・`tar tzf` で数えた（v0.5.3）。
- `git ls-files`、`git count-objects -vH`、`git log -- data/ema.sqlite3` で、追跡しているファイルと履歴の量を測った。
- uv のキャッシュ（`~/.cache/uv/git-v0/`）の大きさを `du` で測った（uv 0.12.9）。
- 参照元：
  - [MCP Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture)：stdio トランスポートと、tools・resources・prompts の区別。
  - [FastMCP Resources](https://gofastmcp.com/servers/resources)：resource は URI で識別する読み取り専用のデータで、LLM とクライアントアプリケーションが読む。
  - このサーバーが使う FastMCP は、公式 MCP Python SDK に同梱された `mcp.server.fastmcp` である。単体の `fastmcp` パッケージではない。

## 主な発見

### 1. `uvx --from git+...` の動き

1. uv がリポジトリを指定のタグで取得する。
2. `pyproject.toml` に従って wheel をビルドする。
3. wheel を使い捨ての環境にインストールする。
4. `[project.scripts]` の `ema-rwe-mcp` を起動する。MCP ホストは、このプロセスと標準入出力で通信する。

動作中に使われるのは、手順3でインストールされた wheel の中身だけである。

### 2. wheel と sdist の中身（v0.5.3 の実測）

| 成果物 | 中身 |
|---|---|
| wheel（インストールされるもの） | `ema_rwe/` のコード、`ema_rwe/data/ema.sqlite3`（45.8 MB）、`ema_rwe/data/ema-medicines.json`（6.7 MB）、`ema_rwe/docs/mcp-workflow.md`、dist-info |
| sdist（ソース配布） | 追跡しているファイルのほぼすべて（`docs/` 31、`tests/` 24、`src/` 22、`data/` 6、`scripts/` 2、`AGENTS.md`、`README_DEV.md` など） |

`uvx --from git+...` で使われるのは wheel だけで、sdist は使われない。

### 3. ファイルごとの要否

| ファイル | 起動に必要か | 理由 |
|---|---|---|
| `src/` | 必要 | プログラム本体 |
| `pyproject.toml` | 必要 | ビルドの指示（依存関係、同梱ファイル、起動コマンド） |
| `data/ema.sqlite3`、`data/ema-medicines.json` | 必要 | wheel に同梱するデータ。初回起動時に OS のユーザーデータフォルダへ複製する |
| `docs/mcp-workflow.md` | 必要（v0.5.2 から） | wheel に同梱し、MCP リソース `ema-rwe://docs/mcp-workflow` として返す手順書 |
| `README.md`、`LICENSE`、`NOTICE` | ビルドに必要 | `pyproject.toml` の `readme` と `license-files` が指定しているので、無いとビルドが失敗する。wheel にはメタデータとして入る |
| `.mcp.json.sample`、`.codex/config.toml.sample`、`.env.example` | 不要 | MCP は読まない。利用者が自分のクライアント設定へ写すための見本 |
| `uv.lock` | 不要 | `uvx` は依存関係をその場で解決し直す。開発環境（`uv sync`）を固定するためのもの |
| `data/terminology.example.json`、`data/terminology_decisions.json`、`data/imports/.gitkeep` | 不要 | 利用者辞書の記入例、その判定記録、取込フォルダの目印 |
| `AGENTS.md`、`tests/`、`scripts/`、`docs/`（手順書以外）、`CHANGELOG.md`、`README_DEV.md` | 不要 | 開発・保守のためのファイル。wheel に入らないので、インストールにも起動にも関わらない |

当初の見立て（`src/`、`data`、`.codex/`、`.env.example`、`.mcp.json.sample`、`pyproject.toml` で十分）との違いは次の3点。

- 設定の見本（`.codex/`、`.env.example`、`.mcp.json.sample`）は、MCP の動作には不要である。
- ビルドには `README.md`、`LICENSE`、`NOTICE` が必要である。
- v0.5.2 からは `docs/mcp-workflow.md` も必要である。

### 4. それでも開発用のファイルを push してよい理由

GitHub のリポジトリは、このプロジェクトでは2つの役割を兼ねている。

- **配布の窓口**：利用者の `uvx` が取得する。ここで効くのは上の「必要」なファイルだけである。開発用のファイルは wheel に入らないので、利用者の環境を汚さず、起動も遅くしない。増えるのは取得量だけで、テキストなので小さい。
- **ソースの置き場**：
  - テストは、コードが仕様どおりに動くことの証拠になる。
  - 仕様（`docs/spec/`）と検証記録（`docs/validation.md`）は、変更の理由と経緯を残す。
  - `AGENTS.md` は、Claude Code や Codex がこのリポジトリで作業するときの規則である。
  - 公開リポジトリにテストと文書があるのは標準的で、利用者や貢献者からの信頼にもつながる。

push してはいけないのは、次のものである。

- 秘密情報（API キー、`.env`）
- 手元の DB・取得した PDF・HTTP キャッシュ
- 連絡先欄を含む CSV の原本
- 個人の設定（`.mcp.json`、`.codex/config.toml`、`.claude/`、`.devcontainer/`）

これらは `.gitignore` と gitleaks（秘密情報の検出）で除外している。

### 5. 本当に注意すべき点：同梱 DB の Git 履歴

不要なファイルよりも影響が大きいのは、45 MB の `data/ema.sqlite3` を版ごとにコミットしていることである。

| 指標 | 実測値 |
|---|---|
| `data/ema.sqlite3` のコミット回数 | 8 回 |
| Git の pack（圧縮後の履歴全体） | 17.00 MiB |
| uv の Git キャッシュ（取得した履歴） | 110 MB |
| 版ごとの作業コピー（uv のキャッシュ） | 約 53 MB（版ごとに増える） |

uv は Git ソースを履歴ごと取得する。このため DB を更新するたびに、新しい利用者が最初に取得する量と、既存の利用者のキャッシュが増えていく。現在の規模では問題にならない。

## 選択肢と比較

| 選択肢 | 利用者が取得するもの | 長所 | 短所 |
|---|---|---|---|
| A. 現状維持（Git から `uvx`） | リポジトリの履歴全体と、版ごとの作業コピー | 追加の作業が無い | DB の更新ごとに取得量が増える |
| B. PyPI で配布（`uvx ema-rwe-mcp`） | wheel だけ（約 52 MB） | Git の履歴を取得しない。Python パッケージの標準的な配り方 | PyPI のアカウントと公開手順が要る。wheel の大きさの上限（既定 100 MB）に注意 |
| C. DB を GitHub Release の添付ファイルにし、初回起動時に取得する | コードの履歴と DB 1つ | Git の履歴に DB が残らない | 起動時の通信、取得の失敗、整合性（チェックサム）の検証を作り込む必要がある |
| D. Git LFS | LFS の参照と DB | 履歴が軽くなる | `uvx --from git+...` での LFS 取得の対応を確かめる必要がある |

## 結論・推奨

- 開発用のファイル（`AGENTS.md`、`docs/`、`scripts/`、`tests/`）は、push したままでよい。wheel に入らないので、利用者の動作には影響しない。
- 利用者が増えるか、DB の更新回数が増えて取得量が問題になった時点で、B（PyPI での配布）に移ることを推奨する。コードの変更はほぼ不要で、`.mcp.json.sample` の起動コマンドを `uvx ema-rwe-mcp` に変えるだけで済む。
- 版ごとの公開前には、追跡ファイル同士の整合を release-consistency-audit スキル（ユーザー単位のスキル。タグの作成前にフックが点検を求める）で確かめる。

## 未解決事項

- uv が Git ソースを浅い取得（shallow）にする設定を持つかは確かめていない。キャッシュの実測では、履歴全体を取得していた。
- PyPI の公開手順（Trusted Publishing など）と、wheel の大きさの上限の現状は確かめていない。
