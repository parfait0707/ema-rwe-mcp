# 関連語のカタログ語彙からの導出（監督ループ）

## 与えられた指示

`data/terminology.json` の `related_terms` が肝障害だけ実データに合わせて手で調整されていた点について、案 a（カタログの Outcomes／Medicinal condition 列から関連語候補を集計し、全概念へ同じ手順で反映）を実施する。作業は Sonnet、検証は Opus、監督は Fable のループで回す。

## 進め方

| 巡 | 作業（Sonnet） | 検証（Opus） | 監督の判断 |
|---|---|---|---|
| 1 | スクリプト新規作成、候補 775 件、採用 76 件 | 条件付き合格。採用の 1/3 が候補外の手書き、`PSA`／`MM`／`CD`／`MI`／`PE`／`eGFR` が誤検出率 53〜100%、`a/A>=0.10` と広義種語による取りこぼし | 設計変更 1〜9（アンカーを英語名のみに、既収録判定を新規一致ゼロに、閾値緩和、略語規則、概念境界・候補由来の機械検査、HEAD 辞書からの再採掘） |
| 2 | 設計変更 1〜9 を実装、採用 64 件 | （監督の受領検査）治療薬・曝露・一般語が残存。mining の新規判定と本番検索（FTS NEAR）の方式差 | 設計変更 10〜12（新規判定を本番 FTS に統一、10 語の不採用、判定の引き継ぎ） |
| 3 | FTS 整合を実装、採用 34 件 | 条件付き合格。誤検出率 100% の 11 語、50% 超の 3 語、`dedup_subsumed` の重複トークン問題、`hba1c` 消滅理由の誤記、文書の不整合 | 判断 13〜19（15 語の不採用、採否規則の追加と標本確認、重複トークン除外と上位 60 件、判定記録の正本化、文書の全面改稿） |
| 4 | 全指摘を処理、採用 23 語 | 条件付き合格。標本確認規則が引き継ぎ語に未適用（4 語が閾値超）、記録の誤り 2 件、文書の事実誤り、順列重複の候補 5.6% | 判断 20〜24（規則の明文化と全語への遡及、順列畳み込み、判定記録の圧縮、文書訂正） |
| 5 | 全指摘を処理、採用 19 語（11 概念）、候補 2,839 件 | 最終確認（監督役の受領検査＋Opus の短い再検証） | 受領後にコミット |

## 実装・変更内容

- 新規: `scripts/mine_terminology.py`、`tests/test_terminology_mining.py`、`data/terminology_decisions.json`（コミット対象）、`docs/research/202609140758_terminology_mining.md`
- 更新: `data/terminology.json`（`related_terms` のみ追加）、`.gitignore`（`!data/terminology_decisions.json`、`docs/agent_brief/`・`docs/agent_report/` を追跡外に）、README、docs/clinical-search.md、docs/validation.md
- 指示書と報告書は `docs/agent_brief/`、`docs/agent_report/`（Git 追跡外）

## 次のアクション・懸念事項

- `b_new` を加味した候補の順位づけ（`thromboembolic events` のような一般語の扱い）は未導入。
- 出血系 3 概念（intracranial／gastrointestinal／major bleeding）は英語名の広さから互いの研究を共有する。概念設計の見直し候補。
- 辞書コードの臨床専門家による確認は未実施。

## 追記（2026-09-15）: 未収録語のログ

- 日本語を含み辞書・語彙グループ・医薬品辞書のいずれにも一致しない質問・検索語を `data/terminology_unmatched.json` に記録（`service.record_unmatched`）。`plan_study_search`／`search_studies`／`compare_protocols` の 3 経路。`catalogue_status.unmatched_terms` に上位 20 件。`EMA_UNMATCHED_LOG_PATH` で変更可、Git 管理外。
- テスト `tests/test_unmatched.py` 3 件。全体 190 passed。
