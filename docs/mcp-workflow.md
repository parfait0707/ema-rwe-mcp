# MCP workflow for callers

The MCP `instructions` string is deliberately short. This file is the full procedure that a calling
agent (Claude Code, Codex, any MCP client) follows. It is written in English because it is read by
the model that drives the tools; the rest of `docs/` is in Japanese.

## 1. Plan

- `catalogue_status` once per session. `status=current` and `source_type_imports` containing
  `claims`, `ehr`, `registry` means the committed catalogue is usable. Only when no CSV was ever
  imported, or a stale snapshot yields no candidates, may a visible user-initiated browser export be
  imported with `import_catalogue_csv`.
- `plan_study_search(question)` translates Japanese concepts through the committed dictionary
  (`data/terminology.json`) and the EMA medicines dictionary. It returns `queries`, `english_terms`,
  `related_terms` and typed `code_candidates`. Dictionary codes are retrieval hints with
  `verification=unverified`; confirm vocabulary, version and role in the PDF.
- If `status=needs_client_translation`, translate yourself and pass `synonyms` / `codes`. The
  unrecognised Japanese text is logged (`unmatched_logged=true`); `catalogue_status.unmatched_terms`
  lists the most frequent ones so the dictionary can be extended later.
- Medicines: expand product names to INN/common names and ATC codes; never equate a class with a
  member or a combination with one ingredient.

## 2. Screen with `compare_protocols`

- Pass ALL query variants in `queries`; the candidate count is the deduplicated union.
- Matching is local FTS5: a multi-word query must co-occur within three tokens of one column
  (`NEAR`); single words match as tokens; variants are OR-ed. Dictionary terms and code variants are
  added automatically.
- `role` scopes the columns: `outcome` searches the catalogue Outcomes, Main study objective, title
  and saved definitions; `condition` searches Medicinal condition and title; `exposure` searches
  product names, INN, ATC, protocol data sources and title. Use it whenever the question names a role
  ("as an outcome", "in patients with", "exposed to"). `any` searches everything.
- `darwin_only=false` unless the user restricts to DARWIN EU.
- Counts are local-index counts, never EMA coverage or verified PDF eligibility.

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
