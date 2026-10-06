# 公開リリース手順（v0.1.1）

## 公開前チェック（実施済み・要確認）

| 項目 | 状態 |
|---|---|
| 機密情報の走査（現在のツリーと全履歴で APIキー・トークン・個人パス） | 実施済み。検出なし。`E:/codex/...` の個人パスは `<checkout>` に置換 |
| 機械固有の設定 | `.codex/config.toml` は `.codex/config.toml.sample` に変更し、実体は `.gitignore` |
| エージェント・エディタの状態 | `.claude/`, `.devcontainer/`, `.serena/`, `.mcp.json`, `CLAUDE.md`, `.python-version` を `.gitignore` |
| ライセンス | `LICENSE`（MIT、著作権者 parfait0707）。**別のライセンスにする場合はタグ前に差し替える** |
| パッケージ情報 | `pyproject.toml` 0.1.0、readme / license / urls / classifiers |
| 同梱データ | `data/ema.sqlite3`（42 MB、連絡先列なし）、`data/ema-medicines.json`（wheel同梱はこの2つ）。記入例`data/terminology.example.json`と`data/terminology_decisions.json`はリポジトリのみ |
| ドキュメント | `README.md`（利用者）、`README_DEV.md`（開発者）、`CHANGELOG.md`、`docs/` |
| Git 履歴 | 作業ログ（`docs/work_log/`）はツリーから削除し `.gitignore` 済み。**履歴からの除去は下記「履歴の書き換え」を公開前に実行する**（コミットハッシュが変わり、タグは再作成、GitHub の PR ページには旧コミットが残りうる） |

## 履歴の書き換え（作業ログの除去。公開前に 1 回、手元で実行）

```bash
git switch main && git pull origin main
git bundle create ../rwd-catalogue-backup.bundle --all          # 復旧用バックアップ
uvx git-filter-repo --path docs/work_log --invert-paths --force # 全履歴から docs/work_log を除去（origin remote が外れる）
git remote add origin https://github.com/parfait0707/ema-rwe-mcp.git
git push --force origin main
git tag -d v0.1.0 v0.1.1 2>/dev/null; git push origin --delete v0.1.0 v0.1.1 2>/dev/null
git tag -a v0.1.1 -m "v0.1.1 first public release" && git push origin v0.1.1
```

注意: GitHub は PR の参照（refs/pull/*/head）で旧コミットを保持するため、旧履歴の完全消去には
リポジトリの再作成か GitHub サポートへの依頼が必要です。確実に消したい場合は新しいリポジトリへ
書き換え後の履歴だけを push する方法が最も簡単です。

## 公開手順

```bash
git switch main && git pull origin main
uv run pytest -q && uv run ruff check src tests      # green を確認
git tag -a v0.1.1 -m "v0.1.1 first public release" && git push origin v0.1.1
gh release create v0.1.1 --title "v0.1.1" --notes-file CHANGELOG.md
gh repo edit parfait0707/ema-rwe-mcp --visibility public --accept-visibility-change-consequences
```

## 以降のリリース（例：v0.2.0）

1. `pyproject.toml` の `version`、`uv lock`、`README.md`・`README_DEV.md` の版表記、`.mcp.json.sample`・`.codex/config.toml.sample` の固定タグ、`EMA_USER_AGENT` の既定値、`CHANGELOG.md` の見出しを揃え、PR でマージする。
2. main で `uv run pytest -q`、`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv build` が green であることを確認し、`git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`。
3. `gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <その版の CHANGELOG 節>`。
4. 下記「公開後の確認」の 1 を新しいタグで行う。

## 公開後の確認

1. 資格情報のない環境で `uvx --from git+https://github.com/parfait0707/ema-rwe-mcp@v0.1.1 ema-rwe --help` が動く。
2. `.mcp.json.sample` をコピーした Claude Code から `catalogue_status` が `status: current` を返す。
3. README の Issues リンクが開く。Issue テンプレートは必要に応じて追加する。

## 公開後に残る作業

- `docs/validation.md` の「未検証」項目（q3〜q6 の A/B、しおりのない PDF の読取量）。
- 種別タグ付きカタログの更新手順を利用者向けに簡略化する（現状は 4 回の CSV export が必要）。
