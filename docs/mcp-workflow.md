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

- Pass one **block** per concept: `blocks: [{role, queries, category_terms}]`. Terms within a block are
  OR-ed; blocks are AND-ed. A single-concept question may use `queries`, `role` and `category_terms`
  instead. With blocks, put every term in its block (no global `synonyms`/`codes`).
- `queries`: every specific name and paraphrase (ICD-10 titles and inclusion terms, clinical names,
  abbreviations, singular/plural, INN or product names). The candidate count is the deduplicated
  union.
- `category_terms`: the umbrella a catalogue record may use instead of the specific name — the ICD-10
  block or chapter title, composite outcomes (MACE, cardiovascular events, adverse events of special
  interest, immune-related adverse events, pregnancy outcomes). Keep them specific to the concept;
  generic phrases such as "adverse drug reactions" only add noise.
- Medicines: the server resolves each query to ATC codes (catalogue entries such as `(B01AF02) apixaban`
  and the EMA medicines dictionary) and searches names only; ATC is a join key, not an answer. An EMA
  record's ATC code is never a catalogue search term (the catalogue may give that code to another
  medicine); it remains a hint for `search_protocol_text` inside a protocol PDF.
  - A medicine adds its catalogue name as a query and its 4th-level class (code and catalogue name) as
    category terms. A class match is not the medicine itself: confirm the exposure in the PDF.
  - A requested class (pass its name and its 3rd/4th-level ATC code, e.g. `N03A`, as queries) adds the
    names of every member coded under it as queries, labelled `catalogue_atc` or `ema_medicines`.
  - `medicine_expansion` lists what was added per query (at most 100 names per question, class names
    first; `omitted` counts the rest). Medicines in neither source get no expansion:
    give their names (and class members) yourself.
- Every column is searched. `role` ranks matches in that role's catalogue columns (Outcomes, Medicinal
  condition, INN/ATC) first instead of filtering, because 19% of records have an empty Outcomes field.
- Candidates are ranked, never cut: specific matches before category-only ones, role-column matches
  first, secondary-use data before surveys, then fused BM25 rank. Each candidate carries
  `rank_features`, `matched_terms` and `matched_term_sources` (`category` for umbrella matches).
- `check_protocols=N` (0–20) checks the Study documents of the top N listed candidates (no PDF) and
  ranks studies without a protocol last. Use it before asking the user to pick `study_ids`.
- Without any request, candidates whose export lists no protocol (`protocol_listed=false`) rank after
  listed ones of the same specificity and role, and a study whose downloaded protocol has no text layer
  (`protocol_text_layer=none`, image-only, needs OCR) or whose retrieval found no protocol
  (`protocol_found=false`) ranks last. Nothing is removed: when a selected study
  turns out to have no protocol or an unreadable one, say so and take the next candidate.
- `observed_exposures` lists medicines filled in for studies whose export lists none, with their source
  (`catalogue_text`: the title or description, possibly a comparator; `protocol_pass_table`: the
  protocol's PASS information table, with page). Confirm the role in the protocol before reporting it.
- Medicines: give the INN and the EU and US product names (each query is matched as one whole name), plus
  abbreviations (TNFi, JAKi, DOACs), noun variants (drugs/medicines/medications) and hyphenated and
  unhyphenated spellings. For a medicine absent from the catalogue also give its 5th-level ATC code: a
  zero-hit result then offers its ATC class and the members the catalogue records as analogous concepts.
  When a product was requested, tell the user which candidates matched only its ingredient (another
  product of the same substance).
- Matching is local FTS5: the words of a multi-word term must co-occur in one column (`NEAR`, distance
  max(3, words - 1); a query's own distance also covers its stop words). Each query variant stays one
  phrase however long. `search_studies` still splits a query of five or more content words into single
  words and filters by `role`, so pass sentence-style questions there, not to `compare_protocols`.
- ICD-10 codes rarely appear in catalogue records (code-only relative recall 0.010 on the gold set);
  they matter when reading protocol PDFs.
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
- Analogous candidates are screened and ranked like concept ones (one block of the analogous terms);
  `matched_term_sources` says whether a term came from the server's ATC class (`catalogue_atc`), from
  you (`caller`) or from a user dictionary (`dictionary`).
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
