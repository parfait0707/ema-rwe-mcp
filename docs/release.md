# 公開リリース手順（v0.1.1）

## 公開前チェック（実施済み・要確認）

| 項目 | 状態 |
|---|---|
| 機密情報の走査（現在のツリーと全履歴で APIキー・トークン・個人パス） | 実施済み。検出なし。`E:/codex/...` の個人パスは `<checkout>` に置換 |
| 機械固有の設定 | `.codex/config.toml` は `.codex/config.toml.sample` に変更し、実体は `.gitignore` |
| エージェント・エディタの状態 | `.claude/`, `.devcontainer/`, `.serena/`, `.mcp.json`, `CLAUDE.md`, `.python-version` を `.gitignore` |
| ライセンス | `LICENSE`（MIT、著作権者 parfait0707）。**別のライセンスにする場合はタグ前に差し替える** |
| パッケージ情報 | `pyproject.toml` 0.1.0、readme / license / urls / classifiers |
| 同梱データ | `data/ema.sqlite3`（42 MB、連絡先列なし）、`data/ema-medicines.json`、`data/terminology.json`、`data/terminology_decisions.json` |
| ドキュメント | `README.md`（利用者）、`README_DEV.md`（開発者）、`CHANGELOG.md`、`docs/` |
| Git 履歴 | 公開すると全履歴が見える。`docs/work_log/` の作業ログ（コスト・所要の実測）も含まれる。非公開にしたい場合は公開前に履歴の書き換えが必要（本手順では行わない） |

## 公開手順

```bash
git switch main && git pull origin main
uv run pytest -q && uv run ruff check src tests      # green を確認
git tag -a v0.1.1 -m "v0.1.1 first public release" && git push origin v0.1.1
gh release create v0.1.1 --title "v0.1.1" --notes-file CHANGELOG.md
gh repo edit parfait0707/rwd-catalogue-mcp --visibility public --accept-visibility-change-consequences
```

## 公開後の確認

1. 資格情報のない環境で `uvx --from git+https://github.com/parfait0707/rwd-catalogue-mcp@v0.1.1 ema-rwe --help` が動く。
2. `.mcp.json.sample` をコピーした Claude Code から `catalogue_status` が `status: current` を返す。
3. README の Issues リンクが開く。Issue テンプレートは必要に応じて追加する。

## 公開後に残る作業

- `docs/validation.md` の「未検証」項目（q3〜q6 の A/B、しおりのない PDF の読取量）。
- 種別タグ付きカタログの更新手順を利用者向けに簡略化する（現状は 4 回の CSV export が必要）。
