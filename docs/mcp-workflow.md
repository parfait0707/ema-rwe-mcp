# MCP workflow for callers

The MCP `instructions` string is deliberately short. This file is the full procedure that a calling
agent (Claude Code, Codex, any MCP client) follows. It is written in English because it is read by
the model that drives the tools; the rest of `docs/` is in Japanese.

## 1. Plan

- `catalogue_status` once per session. `status=current` and `source_type_imports` containing
  `claims`, `ehr`, `registry` means the committed catalogue is usable. Only when no CSV was ever
  imported, or a stale snapshot yields no candidates, may a visible user-initiated browser export be
  imported with `import_catalogue_csv`.
- No disease dictionary ships. `plan_study_search(question)` translates design/method words and
  medicines (EMA medicines dictionary) and, when the user configured one, concepts of their own
  dictionaries (`data/dictionaries/*.json` or `EMA_TERMINOLOGY_PATH`; `catalogue_status.dictionaries`).
- `status=needs_client_translation` (every Japanese question without a matching user-dictionary
  concept): follow `client_expansion.instruction`. Generate English `queries`, `synonyms`, `codes` and
  `analogous_terms` yourself, exploring synonyms based on ICD-10 (WHO 2019): category and subcategory
  titles and inclusion terms as synonyms, never Excludes terms; other categories of the same block as
  `sibling` analogous terms, the block/chapter as `broader`. Your codes are unverified retrieval hints;
  confirm vocabulary, version and role in the PDF.
- Result rows carry `matched_term_sources` (`query` = the query string itself, `caller` = your synonyms, `llm` = server LLM codes/synonyms, `vocabulary`, `dictionary:<file>`,
  `ema_medicines`) so you can say which term, from where, retrieved each study.
- The unmatched-query log (`catalogue_status.unmatched_terms`) is written only while a user dictionary
  is configured.
- Medicines: expand product names to INN/common names and ATC codes; never equate a class with a
  member or a combination with one ingredient.

## 2. Screen with `compare_protocols`

- Pass ALL query variants in `queries`; the candidate count is the deduplicated union.
- Matching is local FTS5: the words of a multi-word term must co-occur in one column (`NEAR`, distance
  max(3, words - 1); a query's own distance also covers its stop words, so a title such as "Malignant
  neoplasm of bronchus and lung" matches itself); single words match as tokens; variants are OR-ed. Each
  `compare_protocols` query variant stays one phrase however long, so full ICD-10 titles are safe
  queries. `search_studies` still splits a query of five or more content words into single words, so
  pass sentence-style questions there, not to `compare_protocols`. Dictionary terms and code variants are
  added automatically.
- `role` scopes the columns: `outcome` searches the catalogue Outcomes, Main study objective, title
  and saved definitions; `condition` searches Medicinal condition and title; `exposure` searches
  product names, INN, ATC, protocol data sources and title. Use it whenever the question names a role
  ("as an outcome", "in patients with", "exposed to"). `any` searches everything.
- `darwin_only=false` unless the user restricts to DARWIN EU.
- Counts are local-index counts, never EMA coverage or verified PDF eligibility.

## 2b. Zero hits: analogous concepts

- Every result row carries `match_basis` and `matched_terms`. Report them when a candidate matched only
  through an expansion term.
- When the requested concept has no study, the response carries `analogous_fallback` (no PDFs fetched):
  analogous concepts with `relation` (broader/sibling/associated) and `study_count`. Tell the user that no
  study of the requested concept exists in the local index, show these concepts, and on agreement rerun
  `compare_protocols` with `match_scope="analogous"`, the same queries and filters, and a `question` that
  names the analogous concept.
- `status=concept_filtered_out`: studies of the requested concept exist in the index
  (`concept_index_matches_before_filters`) but filters, `darwin_only`, `status` or `analyzed_only` removed
  them. Say so and offer to relax those first; the analogous concepts are only an alternative.
- `status=no_analogous_terms`: propose clinically analogous English concepts yourself and pass them as
  `analogous_terms: [{term, relation}]` with `match_scope="analogous"`.
- Present every analogous result as "defined this way for the analogous concept X (relation)", never as
  the requested concept. The comparison table's first row states the match basis.
- If screened protocols of the requested concept lack the definition asked for, offer the same
  `match_scope="analogous"` search; the server does not detect this automatically.

## 3. Narrow when `status=needs_narrowing`

Ask the user, in one message, for BOTH:

1. a catalogue source type: `claims`, `ehr`, `registry` or `others` (outside the three filtered
   exports), quoting `facets.data_source_types`;
2. study countries, quoting the top of `facets.countries`.

Also offer `facets.conditions` (Medicinal condition values), `facets.study_designs` and a narrower
`role` as further filters. Pass the answers as `filters` and rerun with the SAME `queries`.

When `candidates_listed=true` (total within `max_listed_candidates`, default 50) the response carries a
compact `candidates` list. Show it and let the user choose explicit `study_ids` (at most
`max_screening_studies`, default 5). Pass `study_ids` to `compare_protocols` with the same queries and
filters. Never pick a subset yourself.

## 4. Process within the screening limit

- Every row has `pending_tools`. Run all of them: `analyze_protocol` (read every `next_offset`
  batch), `cache_protocol_analysis`, `research_protocol`, `cache_protocol_answer`.
- `analyze_protocol` may return `status=extracting` (server-side provider extraction running):
  call it again for the same study until it returns the analysis; do not extract client-side.
- With `needs_client_extraction`, cache each batch via `cache_protocol_analysis(batch_offset=offset)`
  so progress survives context compaction; skip offsets listed in `cached_batch_offsets`; finish with
  `coverage_complete=true`. If your client can run a cheaper subagent (Claude Code: `Agent` with
  model sonnet), delegate one study's batch reading and caching to it and keep only results in
  the main context.
- Quotes must be verbatim; pages are physical PDF pages; keep `usage` honest
  (`used`/`planned`/`candidate`/`unclear`). A planned source is never "used".
- `source_preference` (types, definition role, `mode=prefer` by default) ranks PDF evidence after
  screening. It is independent of the catalogue source-type filter.
- Call `get_protocol_comparison`. If `selection_status=needs_selection`, show `screening_summary`
  and ask for `selected_study_ids` within `max_comparison_studies`.
- Present the comparison table, JSON paths, failures and missing information.

## 5. Follow-up questions

`search_protocol_text`, `get_protocol_outline`, `read_protocol_text`, `research_protocol` and
`cache_protocol_answer` work on archived PDFs by immutable `protocol_id` without EMA requests. A
zero-hit search does not prove absence; inspect methods, outcome/exposure definitions and code-list
appendices.

## Response shapes and context budget

- `search_studies` returns compact rows by default (`detail=compact`): id, title, status, countries,
  source types, designs, conditions, score, analysis flags, protocol id. `detail=full` adds
  descriptions, provenance and the full query expansion.
- `compare_protocols` above the limit returns facets, candidates (when listed) and per-query
  `query_expansions` summaries only; no PDFs are fetched.
- `catalogue` inside search responses carries only `status`, `source_type_imports` and
  `browser_refresh_recommended`; call `catalogue_status` for directories and limits.
