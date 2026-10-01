# EMA RWD Catalogue の利用規約と公開可否の調査（2026-10-01）

法的助言ではない。一次資料（2026-10-01 取得）と、このリポジトリの実装・同梱物の照合結果である。

## 一次資料（逐語）

| 資料 | 要点 |
|---|---|
| [Catalogue Legal notice](https://catalogues.ema.europa.eu/legal-notice) | "These webpages are © EMA [1995 - 2024]. Third parties may own copyright on the documents published on this site. Permission for their reproduction must be obtained from the relevant copyright holder/s." / 引用は "list it with the URL, and the month and year you accessed it" / ロゴは事前の書面許可なく使用禁止 / リンクは "endorsement of any commercial product or service" を示唆しないこと。**一般的な再利用許諾の条項はない** |
| [Catalogue Terms (/legal)](https://catalogues.ema.europa.eu/legal) | 登録者は "all information is accessible to the public" を了承する（公開の了承であり、第三者への利用許諾ではない） |
| [EMA Legal notice](https://www.ema.europa.eu/en/about-us/about-website/legal-notice) | "may be reproduced and / or distributed ... for non-commercial and commercial purposes, provided that EMA is always acknowledged as the source ... included in each copy" / "do not apply to content supplied by third parties" |
| [robots.txt](https://catalogues.ema.europa.eu/robots.txt) | `Disallow: /search/` など Drupal 既定。`/study/`・`/system/files` は禁止されていない。Crawl-delay なし |
| [User guide EMA/198777/2025](https://www.ema.europa.eu/en/documents/regulatory-procedural-guideline/user-guide-hma-ema-catalogues-real-world-data-sources-studies_en.pdf) | §8.11 "Search results can be exported by clicking on the 'Export results' button" / ログイン不要で閲覧・ダウンロード可 / プロトコルは全版が公開 |
| [Data Protection Notice EMA/60093/2024](https://www.ema.europa.eu/en/documents/other/european-medicines-agencys-data-protection-notice-hma-ema-catalogue-real-world-data-studies_en.pdf) | 研究責任者・連絡先の氏名と連絡先は "Published online"。公益目的の公開登録簿（Reg. 2018/1725 Art 50(1)(g)） |
| GVP Module VIII Rev 3 VIII.B.2 | PASS プロトコルは登録簿で公開。知的財産保護のための黒塗り版の事前登録を認める |

見つからなかったもの：スクレイピング・自動アクセス・API・一括 export を明示的に制限する条項。Commission Decision 2011/833/EU の EMA への適用（EUR-Lex は取得不可）。

## 実装・同梱物の照合

| 項目 | 現状 | 評価 |
|---|---|---|
| HTTP アクセス | `http.py` が robots.txt を解析して遵守、リダイレクト先も検査、2 秒間隔、User-Agent `ema-rwe-mcp/0.3` | 適合 |
| 検索結果ページ | HTTP では取得しない。CSV は利用者が表示ブラウザで Export Results を 1 回押す | 利用者の操作であり、ガイドが案内する手順と同じ。Playwright 経路は `/search` を開くため、クローラーではない点を明記し続ける |
| プロトコル PDF | 選択した研究だけ、各利用者の手元で取得・保持。リポジトリ・wheel に PDF はない | 適合（第三者著作物を再配布していない） |
| 個人データ | 連絡先列（First/Last name、ORCID）は取り込まない。同梱 DB にメールアドレス 0 件。生 CSV は `data/raw/`（git 管理外）だけ | 適合 |
| 同梱 DB（3,314 件） | 題名・目的・説明・アウトカム・解析計画などカタログ本文を再配布 | **要対応**。EMA の一般許諾（出典明記）は使えるが、カタログ固有の告知に許諾条項がなく、登録者入力が第三者コンテンツに当たる可能性がある |
| テスト fixture | `tests/fixtures/gold/pool/*.csv` にカタログ本文、`ema_protocol_cards.html` にカタログ HTML 断片 | 同梱 DB と同じ扱い |
| 出典表示 | README に「公式 CSV export（2026-10-01）から作成、利用条件は EMA 規約に従う」 | 不足：© EMA の明記、URL と取得年月の引用形式、wheel 内の表示、MIT がデータに及ばない旨 |
| 名称 | リポジトリ・パッケージ名に "EMA" を含む。ロゴは不使用 | 要対応：EMA と無関係で承認を受けていない旨の免責 |

## 結論

公開は継続してよい水準だが、次の 3 点を対応すべき。

1. 出典表示：README と wheel 同梱物（NOTICE か DB メタデータ）に「Source: HMA-EMA Catalogues of real-world data sources and studies, https://catalogues.ema.europa.eu/, accessed October 2026. © EMA. 第三者の著作物を含みうる」を入れる。
2. ライセンスの範囲：MIT はコードのみで、同梱カタログデータと fixture には EMA の条件が適用されると LICENSE/README に書く。
3. 免責：EMA とは無関係で、承認・推奨を受けていないことを README 冒頭に書く。

残るリスクは同梱 DB の再配布で、最も保守的な選択は DB を同梱せず利用者が CSV を export して取り込む方式に戻すこと（初回体験は悪化する）。確実を期すなら EMA に再利用可否を問い合わせる。
