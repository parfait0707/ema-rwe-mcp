# リリース手順

冒頭の 3 節（公開前チェック、履歴の書き換え、公開手順）は v0.1.1 の初回公開時の記録で、実施済みである。再実行しない（履歴の書き換えと公開範囲の変更は破壊的）。以降の版は「以降のリリース」と「月ごとのカタログのスナップショット」に従う。

## 公開前チェック（v0.1.1 の記録）

| 項目 | 状態 |
|---|---|
| 機密情報の走査（現在のツリーと全履歴で APIキー・トークン・個人パス） | 実施済み。検出なし。`E:/codex/...` の個人パスは `<checkout>` に置換 |
| 機械固有の設定 | `.codex/config.toml` は `.codex/config.toml.sample` に変更し、実体は `.gitignore` |
| エージェント・エディタの状態 | `.claude/`, `.devcontainer/`, `.serena/`, `.mcp.json`, `CLAUDE.md`, `.python-version` を `.gitignore` |
| ライセンス | `LICENSE`（MIT、著作権者 parfait0707）。**別のライセンスにする場合はタグ前に差し替える** |
| パッケージ情報 | `pyproject.toml` 0.1.0、readme / license / urls / classifiers |
| 同梱データ | `data/ema.sqlite3`（42 MB、連絡先列なし）、`data/ema-medicines.json`（wheel同梱はこの2つ）。記入例`data/terminology.example.json`と`data/terminology_decisions.json`はリポジトリのみ |
| ドキュメント | `README.md`（利用者）、`README_DEV.md`（開発者）、`CHANGELOG.md`、`docs/` |
| Git 履歴 | 作業ログ（`docs/work_log/`）はツリーから削除し `.gitignore` 済み。**履歴からの除去は下記「履歴の書き換え」で公開前に実行した**（コミットハッシュが変わり、タグは再作成、GitHub の PR ページには旧コミットが残りうる） |

## 履歴の書き換え（v0.1.1 の公開前に 1 回実施済み。再実行しない）

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

## 公開手順（v0.1.1 の記録）

```bash
git switch main && git pull origin main
uv run pytest -q && uv run ruff check src tests      # green を確認
git tag -a v0.1.1 -m "v0.1.1 first public release" && git push origin v0.1.1
gh release create v0.1.1 --title "v0.1.1" --notes-file CHANGELOG.md
gh repo edit parfait0707/ema-rwe-mcp --visibility public --accept-visibility-change-consequences
```

## 以降のリリース（vX.Y.Z）

1. `pyproject.toml` の `version`、`uv.lock` のこのパッケージの版、`README.md`・`README_DEV.md` の版表記、`.mcp.json.sample`・`.codex/config.toml.sample` の固定タグ、`EMA_USER_AGENT` の既定値（`src/ema_rwe/config.py`、`.env.example`、`README_DEV.md` の 3 か所）、`CHANGELOG.md` の見出しを揃え、PR でマージする。
   - 同梱のカタログを、最新の公開済みスナップショットで置き換える（spec v1.19。同梱済みと同じ日付なら下のとおり何もしない）：`gh release download data-<YYYYMMDD> -D <一時フォルダ>` の後、`uv run scripts/build_catalogue_snapshot.py --verify <一時フォルダ> --unpack-to data/ema.sqlite3` を実行し、`data/ema.sqlite3` をコミットする（検証に通らなければ何も置き換わらない。置き換えの前に `data/ema.sqlite3` の `-wal`／`-shm` を消すので、この DB を開いているサーバー（checkout から起動した MCP など）は先に止める）。`NOTICE` と `README.md` のエクスポートの日付と件数を合わせる。最新のスナップショットが同梱済みの日付と同じなら、何もしない。医薬品辞書は `uv run ema-rwe refresh-drugs --force` で `data/ema-medicines.json` を更新できる。
2. main で `uv run scripts/check.py` が通ることを確認し（版の表記の一致、`uv.lock`、ruff、pytest、抽出の正解データ、ビルド、wheel の中身を順に確かめる）、読み手の抽出を正解データで採点する（`uv run scripts/extraction_eval.py catalogue --online` の後、内部 LLM なら `extraction_eval.py run`、呼出元抽出なら 4 つの研究を正解の質問で解析してから `extraction_eval.py grade`。下限を下回ったとき、または種類の取り違え（`misplaced`）が正解ファイルの `maximum.misplaced` を超えたときは失敗になるので、リリースの前に原因を調べる。spec v1.20、v1.24）。その後、`git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`。保守者の Claude Code の環境では、利用者レベルのフック（リポジトリには無い）が、タグとリリースの作成の前に整合性の監査を済ませ、コミットした状態に印を付けることを求める（スナップショットの公開も同じ）。
3. `gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <その版の CHANGELOG 節>`。
4. 下記「公開後の確認」の 1 を新しいタグで行う。

## 月ごとのカタログのスナップショット

カタログはアプリの版とは別に、月ごとのスナップショットとして GitHub Releases に公開する（[計画](research/202610081527_code_data_versioning_plan.md)。段階 1〜5 は実装済み）。利用者のサーバーは、起動時に確かめて取り込む（spec v1.18）。`data/ema.sqlite3` は月ごとにはコミットしない（spec v1.19。アプリのリリースのときに、上の手順で最新のスナップショットに置き換える）。

作業はリポジトリの外の作業用フォルダで行う（`<作業>` と書く）。`EMA_DB_PATH=<作業>/ema.sqlite3` と `EMA_IMPORT_DIR=<作業>/imports` を設定しておく。

1. 作業用の DB を、最新の公開済みスナップショットにする：`uv run ema-rwe update-catalogue`。前のスナップショットの補完の結果（プロトコルから補った医薬品）が、この DB に入る。
2. EMA の検索ページから、全研究と claims、ehr、registry の 4 つを同じ日にエクスポートし、`<作業>/imports/studies/<YYYYMMDD>_all_export-data.csv` と `<作業>/imports/source_type/<YYYYMMDD>_<種別>_export-data.csv` に置く。
3. `uv run ema-rwe import-all` で作業用の DB に取り込む。新しい研究の医薬品を補うなら、`uv run ema-rwe backfill-protocols` を実行する（1 件ずつ、間隔を空けて取得する。429 や障害で止まり、再開できる）。
4. `uv run scripts/build_catalogue_snapshot.py <YYYYMMDD> --import-dir <作業>/imports --observations-from <作業>/ema.sqlite3` を実行する。その日付の 4 つだけを空の DB に取り込み、作業用の DB の補完の結果を移し、`VACUUM` と整合性の確認をして、`dist/snapshots/<YYYYMMDD>/` に DB、gzip、`manifest.json` を書き出す。配布する事実を載せるときは、先に `uv run ema-rwe export-facts --output <事実の DB> --source-type claims` で解析を書き出し、`--facts-from <事実の DB>` を付ける（スナップショットの研究の分だけが入り、件数は manifest の `prebuilt_facts` で確かめる。spec v1.31）。0 件の表を載せると利用者の手元の配布事実が消えるので、書き出しが 0 件なら `--facts-from` を付けない。4 つのどれかが無いか重複するとき、出力先に同じスナップショットが既にあるとき（作り直すなら先に消す）は止まる。書き出した後、抽出の正解データ（spec v1.20）を新しいカタログで確かめる：正解の研究が Non-interventional study として載っていること、正解の全項目がプロトコルから見つかること。失敗したら終了コード 1 で終わるので、原因を直すまで公開しない（正解のプロトコルは checkout の `data/protocols/` から読む。未取得なら `uv run scripts/extraction_eval.py fetch`。正解データの確認を省く `--skip-gold` は、公開しないスナップショットを試しに作るときだけ使う）。続けて、作業用の DB で `uv run scripts/extraction_eval.py catalogue --online`（最新のプロトコルが正解の文書のままか）と、読み手の抽出の採点（`extraction_eval.py run`、または呼出元抽出の後に `grade`）を行う。
5. `gh release create data-<YYYYMMDD> --target main --latest=false --title "Catalogue <YYYY-MM-DD>" --notes "Catalogue snapshot of the EMA exports dated <YYYY-MM-DD>; see manifest.json" dist/snapshots/<YYYYMMDD>/ema-catalogue-<YYYYMMDD>.sqlite3.gz dist/snapshots/<YYYYMMDD>/manifest.json`。`--latest=false` で、アプリの最新版の表示を変えない。保守者の Claude Code の環境では、利用者レベルのフック（リポジトリには無い）が、リリースの作成の前に整合性の監査を済ませ、コミットした状態に印を付けることを求める。
6. 公開したファイルを取り直して照合する：`gh release download data-<YYYYMMDD> -D <一時フォルダ>` の後、`uv run scripts/build_catalogue_snapshot.py --verify <一時フォルダ>`。
7. 利用者の側から見えることを確かめる：前の日付の DB（手順 1 の時点の複製など）で `uv run ema-rwe update-catalogue --check` が `available` と新しい日付の `latest_snapshot` を返し、`uv run ema-rwe update-catalogue` が `updated` を返す。

スナップショットのスキーマの版がアプリと違うと、利用者のサーバーは取り込まず、`app_update_required` を記録する。スキーマを変えるアプリの版を出したら、その版でスナップショットを作り直して公開する。

## 公開後の確認

1. 資格情報のない環境で `uvx --from git+https://github.com/parfait0707/ema-rwe-mcp@vX.Y.Z ema-rwe --help` が動く。
2. `.mcp.json.sample` をコピーした Claude Code から `catalogue_status` が応答する。`status` は同梱のエクスポートの取り込みから `EMA_CATALOGUE_TTL_SECONDS`（既定 30 日）以内なら `current`、それ以降は `stale` が正常。
3. README の Issues リンクが開く。Issue テンプレートは必要に応じて追加する。

## 公開後に残る作業

- `docs/validation.md` の「未検証」項目（q3〜q6 の A/B、しおりのない PDF の読取量）。
- 種別タグ付きカタログの更新手順を利用者向けに簡略化する（現状は 4 回の CSV export が必要）。
