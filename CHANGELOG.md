# Changelog

## 0.3.2 (2026-10-01)

- Bundled catalogue rebuilt from the 2026-10-01 official exports alone: 3,314 Non-interventional
  studies; claims 819, ehr 916, registry 559, untagged 1,511. Wheel installs refresh their catalogue
  tables on start, keeping cached analyses and answers.

## 0.3.1 (2026-09-28)

- Wheel installs (`uvx`) refresh the catalogue tables of the user's database when a newer package
  bundles a newer catalogue import, in place and in one transaction. Cached analyses and answers are
  untouched; a database the user imported a newer CSV into is kept.

## 0.3.0 (2026-09-28)

Search-logic release. Candidate lists are longer (65 → 144 studies on average in the gold set) but
ranked, so narrow them or pick from the top.

- `compare_protocols` screens concept `blocks` (`{role, queries, category_terms}`, AND-ed) in every
  catalogue column and ranks candidates without cutting any: specific matches before category-only
  ones, role-column matches first, secondary-use data before surveys, then fused BM25 rank
  (`rank_features`). `category_terms` add umbrella names (ICD-10 block, MACE/AESI, ATC group).
  `check_protocols=N` checks the Study documents of the top N candidates and ranks studies without a
  protocol last. On the gold set (provisional labels): relative recall 0.858 → 0.965, relevant studies
  in the top 5 3.60 → 4.10.

## 0.2.1 (2026-09-28)

Bug-fix release. Search-logic changes from the retrieval survey
(`docs/research/202609281003_search_logic.md`) are planned for 0.3.

- `compare_protocols` keeps every query variant as one phrase, however long; only `search_studies`
  still splits free text of five or more content words into single words. A five-word ICD-10 title no
  longer matches on "drug" or "disease" alone (1,212 → 54 candidates in the ILD example).
- NEAR distance grows with phrase length (`max(3, words - 1)`), and a query's own distance counts the
  stop words it drops, so long names and ICD-10 titles such as "Malignant neoplasm of bronchus and
  lung" match their own text.

## 0.2.0 (2026-09-27)

Breaking: no disease dictionary ships or loads by default. Clients that relied on the bundled
Japanese disease dictionary must translate questions themselves (the MCP now tells them how) or place
a dictionary in `data/dictionaries/`.

- The MCP client translates Japanese questions, exploring synonyms based on ICD-10 as
  `plan_study_search.client_expansion` instructs; `needs_client_translation` is returned for every
  Japanese question without a matching user-dictionary concept. The same ICD-10 guidance drives the
  server LLM planner (`use_llm=true`).
- Optional user dictionaries load from `data/dictionaries/*.json` or `EMA_TERMINOLOGY_PATH` (file or
  folder); `catalogue_status.dictionaries` lists them. The former `data/terminology.json` is now the
  format example `data/terminology.example.json`, without WHO ICD-10 titles and not loaded by default.
  The EMA medicines dictionary still ships and loads by default.
- Search rows report `match_basis`, `matched_terms` and `matched_term_sources` (query, caller, llm,
  vocabulary, `dictionary:<file>`, ema_medicines).
- Zero hits return `analogous_fallback`: analogous concepts (broader/sibling/associated) with counts,
  or `concept_filtered_out` when filters removed existing concept studies. `match_scope="analogous"` and
  `analogous_terms` (MCP and CLI `--match-scope` / `--analogous`) screen those studies, labelled
  "類縁概念での一致" in the comparison table's first row.
- The broad clinical groups (diabetes, bleeding, cancer) are removed, so 「1型糖尿病」 no longer
  returns every diabetes study. The unmatched-query log is written only while a dictionary is configured.

## 0.1.1 (2026-09-26)

- `catalogue_status` reports `studies_total` (the whole local catalogue) and labels the per-export `count`,
  so callers no longer quote a single export's row count as the catalogue size.

## 0.1.0 (2026-09-26) — first public release

Evidence-backed MCP server for finding EMA Catalogue non-interventional studies and extracting
study design, cohort, disease/outcome definitions and data sources from the latest protocol PDF,
with physical page numbers and verbatim quotes.

- Install without cloning: `uvx --from git+https://github.com/parfait0707/rwd-catalogue-mcp ema-rwe-mcp`;
  the catalogue snapshot (2026-09-13), the EMA medicines dictionary and the Japanese terminology
  dictionary ship inside the wheel and are copied to the user data directory on first start.
- Two extraction modes: client-assisted (no extra API key; batch checkpoints survive context
  compaction; Claude Code delegates reading to a cheaper subagent) and server-side extraction through
  any LiteLLM provider (parallel batches, polling, verbatim-quote pruning and repair).
- Bookmark-guided reading: chapters from the PDF outline are read first, appendices only when
  referenced or when they hold code lists; PDFs without bookmarks fall back to heading detection.
- Extraction schema 0.3 with a `cohort` block (inclusion/exclusion criteria, index date, baseline,
  follow-up, design-schema pages and time windows) and deterministic audits recorded in
  `missing_information` (redactions, stated-count mismatches, catalogue sources not found,
  unquoted terms).
- Compact tool responses with server-side budgets; `detail=full` returns the whole payload.
- Japanese question support: disease, drug (EMA INN/product/ATC) and code expansion.

Internal history before this release used version numbers 0.1–0.7; see `docs/spec/` and
`docs/validation.md` for the evidence behind each step.
