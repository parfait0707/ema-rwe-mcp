# MCP workflow for callers

The MCP `instructions` string is deliberately short. This file is the full procedure that a calling
agent (Claude Code, Codex, any MCP client) follows. It is written in English because it is read by
the model that drives the tools; the rest of `docs/` is in Japanese. The server serves this file as
the MCP resource `ema-rwe://docs/mcp-workflow` (bundled in the wheel), so a client installed with
`uvx` can read it without a checkout.

## Tool hints

Every tool carries a title and the MCP hints. Read-only (no confirmation needed for what they change):
`catalogue_status`, `search_studies`, `plan_study_search`, `list_local_protocols`, `get_protocol_outline`,
`search_protocol_text`, `read_protocol_text`. Destructive (replace something with no copy kept):
`research_protocol` with `force=true`, `analyze_protocol` with `force_refresh=true` (at `offset=0` it drops
the study's unfinished batches and their answers), `cache_protocol_answer`, `import_catalogue_csv`,
`refresh_drug_dictionary`, and `cache_heading_translations` (it replaces the translations with no copy kept,
and a changed translation drops the study's unfinished extraction batches). Open world (may reach EMA or the
configured LLM): `get_study`, `get_protocol`, `compare_protocols`, `analyze_protocol`,
`cache_protocol_analysis`, `research_protocol`, `refresh_drug_dictionary`, and `plan_study_search` with
`use_llm=true`. A call that asks for progress gets notifications from `compare_protocols` (one per candidate
while `check_protocols` checks Study documents, one per study while protocols are retrieved, then a last one
when all are) and from a server-side `analyze_protocol`
(batches done). A notification that cannot be sent does not stop the tool. An argument name a tool does
not have is refused as a validation error (`additionalProperties: false`), never ignored: fix the name.

## 1. Plan

- `catalogue_status` once per session. `catalogue_snapshot` is the export date of the catalogue, and `update`
  what the server's own look for a newer published snapshot found (`result`: `updated`, `up_to_date`,
  `available`, `kept_local_import`, `app_update_required`, `no_snapshot`, or a failure with its `reason`:
  `unreachable`, `rate_limited`, `invalid_snapshot`, `not_applied`). With
  `update.automatic=true` and `mode=apply` the server takes newer snapshots by itself, so no action follows
  except telling the user about `app_update_required`. With `automatic=false` because of the
  database (one named by `EMA_DB_PATH`, or a checkout's), or with `result=available`, tell the user that
  `ema-rwe update-catalogue` takes the newer snapshot; run it in a shell with the same `EMA_DB_PATH` as the
  MCP client's env (the CLI updates the database of its own environment). With `automatic=false` the server
  does not look at start, so `app_update_available` appears only after that command (or `--check`). With
  `EMA_CATALOGUE_UPDATE=off` (`mode=off`, also `automatic=false`) the user turned updates off: do not
  suggest updating. When `app_update_available` is
  present, tell the user once as its `instruction` says (a newer application release exists; the server never
  replaces itself). With `EMA_CATALOGUE_UPDATE=off` it never appears. Analyses (`source`), answers
  and comparisons (`versions`) record the `app_version`, `catalogue_snapshot` and `parser_version` that
  produced them: quote them when a result differs from an earlier one. `status=current` and `source_type_imports` containing
  `claims`, `ehr`, `registry` means the committed catalogue is usable (freshness is judged per export:
  `exports`, `snapshot_aligned` with `snapshot_note`, `missing_source_type_exports`). Only when no CSV was ever
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
  `ema_medicines`, `catalogue_atc` = a medicine or class name the server added from the catalogue's
  "(ATC code) name" entries, `category` = an umbrella term) so you can say which term, from where,
  retrieved each study.
- The unmatched-query log (`catalogue_status.unmatched_terms`) is written only while a user dictionary
  is configured.
- Medicines: give English INN and product names (and a 5th-level ATC code for a medicine the server
  does not know); the server expands names (section 2). Never equate a class with a
  member or a combination with one ingredient.

## 2. Screen with `compare_protocols`

- Pass one **block** per concept: `blocks: [{role, queries, category_terms}]`. Terms within a block are
  OR-ed; blocks are AND-ed. A single-concept question may use `queries`, `role` and `category_terms`
  instead. With blocks, put every term in its block (no global `synonyms`/`codes`).
- `queries`: every specific name and paraphrase (ICD-10 titles and inclusion terms, clinical names,
  abbreviations, singular/plural, INN or product names). The candidate count is the deduplicated
  union.
- `category_terms`: the umbrella a catalogue record may use instead of the specific name — the ICD-10
  block or chapter title, and the composite or umbrella outcomes studies of that concept use (for
  example MACE or adverse events of special interest; illustrations, not a list to choose from). Keep
  them specific to the concept;
  generic phrases such as "adverse drug reactions" only add noise.
- Medicines: the server resolves each query to ATC codes (catalogue entries such as `(B01AF02) apixaban`
  and the EMA medicines dictionary) and searches names, plus the 4th-level class code the catalogue records; ATC is a join key, not an answer. An EMA
  record's ATC code is never a catalogue search term (the catalogue may give that code to another
  medicine); it remains a hint for `search_protocol_text` inside a protocol PDF.
  - A medicine adds its catalogue name as a query when it names the same medicine (a broader catalogue
    name, without the strain or type the query gives, as a category term; a narrower one not at all)
    and its 4th-level class (code and catalogue name) as category terms. A class match is not the medicine itself: confirm the exposure in the PDF.
  - A requested class (pass its name and its 3rd/4th-level ATC code, e.g. `N03A`, as queries) adds the
    names of every member coded under it as queries, labelled `catalogue_atc` or `ema_medicines`.
  - `medicine_expansion` lists what was added per query (at most 100 names per question, class names
    first; `omitted` counts the rest). Medicines in neither source get no expansion:
    give their names (and class members) yourself.
  - A class expansion (`medicine_expansion[].omitted_members`, total in `omitted_members_total`) lists EMA
    records (their INN/common names) left out because the catalogue gives their ATC code another name: a member named
    differently from its ATC substance name (recombinant factor VIII INNs under `coagulation factor VIII`,
    a vaccine's product name) or an error in the EMA record (a code of an unrelated medicine). When the
    user asked for the class, add each listed name that truly belongs to it to that block's `queries`
    and rerun; leave out the ones that do not.
- Every column is searched. `role` ranks matches in that role's catalogue columns (Outcomes, Medicinal
  condition, INN/ATC, plus the title and related columns) first instead of filtering, because 19% of records have an empty Outcomes field.
- Candidates are ranked, never cut: specific matches before category-only ones, role-column matches
  first, secondary-use data before surveys, then fused BM25 rank. Each candidate carries
  `rank_features`, `matched_terms` and `matched_term_sources` (`category` for umbrella matches).
- `check_protocols=N` (0–20; only when `needs_narrowing` lists candidates, without `study_ids`, with
  `match_scope=concept`) checks the Study documents of the top N listed candidates (no PDF) and
  ranks studies without a protocol last. Use it before asking the user to pick `study_ids`.
- Without any request, candidates whose export lists no protocol (`protocol_listed=false`) rank after
  listed ones of the same specificity and role, and a study whose downloaded protocol has no text layer
  (`protocol_text_layer=none`, image-only, needs OCR) or whose retrieval found no protocol
  (`protocol_found=false`) ranks last. Nothing is removed: when a selected study
  turns out to have no protocol or an unreadable one, say so and take the next candidate.
- `observed_exposures` lists medicines filled in for studies whose export lists none, with their source
  (`catalogue_text`: the title or description, possibly a comparator; `protocol_pass_table`: the
  protocol's PASS information table, with page; a class name or an ATC code written there may stand
  for a medicine the dictionaries lack). Confirm the role in the protocol before reporting it.
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
  names the analogous concept. `match_scope="analogous"` does not combine with `blocks`: move the single
  block's `queries`, `role` and `category_terms` to the top-level arguments. With two or more blocks the
  fallback is `status=not_available_for_blocks`; rerun the concept that has no study on its own.
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

Pass the answers as `filters` (`countries`, `data_source_types`, `study_designs` are the only filter
fields) and rerun with the SAME blocks. `facets.study_designs` may narrow further through
`filters.study_designs`. A `facets.conditions` value (Medicinal condition) is not a filter: to narrow by
it, add it as another block with `role=condition` (blocks are AND-ed). In `compare_protocols` `role`
only ranks; it narrows only in `search_studies`.

When `candidates_listed=true` (total within `max_listed_candidates`, default 50) the response carries a
compact `candidates` list. Show it and let the user choose explicit `study_ids` (at most
`max_screening_studies`, default 5). Pass `study_ids` to `compare_protocols` with the same blocks and
filters. Never pick a subset yourself.

## 4. Process within the screening limit

- The response's top-level `pending_tools` lists `{tool, arguments}` calls. A study with neither an
  analysis nor an answer gets one call, `analyze_protocol` with the `question` (one pass, below); with
  a configured server LLM, or when only the answer is missing, `research_protocol` is listed too. Run
  all of them, each followed by `cache_protocol_analysis` (every `next_offset` batch) or
  `cache_protocol_answer` (pass the `reading` `research_protocol` returned) as their status asks.
- One pass: when `analyze_protocol` was given a `question`, its instruction and `answer_schema` ask you
  to answer it while you read. Pass `question` and that batch's `answer` (`answers=[]` when the batch
  says nothing) with every `cache_protocol_analysis`, one fact per item with its own quote. The
  question never narrows the extraction. The `coverage_complete` call (pass the same `question` again,
  character for character) saves the merged answer and returns `answer_saved`; only when it is `false`,
  call `research_protocol`. A `coverage_complete` call without that `question` while cached batches hold
  answers is refused with `INVALID_INPUT` and saves nothing: repeat it with the `question`.
- While exploring, `search_protocol_text`, `get_protocol_outline` and
  `read_protocol_text` report `source.reading`; if it differs from that `reading`, the heading translations
  changed: call `research_protocol` again and explore afresh.
- A section returned by `search_protocol_text` or `research_protocol` with `continues` is the next (or
  previous) page of the hit it names: read them as one passage (a list of criteria often runs over a page).
  A question's grammatical words (each, from, which, ...) are not searched, and one English word also
  matches its plain plural or singular. A row with `beside` is a neighbour of a short section named for
  the question (`9.3.1` beside `9.3.2 Outcomes definition`): the definitions are often there.
- `analyze_protocol` may return `status=extracting` (server-side provider extraction running):
  call it again for the same study until it returns the analysis; do not extract client-side.
  Finished batches are kept (`progress.reused` counts those not asked again), so calling it again after
  an `LLM_EXTRACTION_FAILED` or a server restart asks the provider only for the batches still missing.
  When every batch was read and the same error comes back on each call (`SCHEMA_VALIDATION_FAILED` "Merged
  extraction exceeds schema limits", or an `EVIDENCE_INVALID` of the merged analysis), the kept batches
  repeat it: call `analyze_protocol` with `force_refresh=true` to read the protocol again.
- `research_protocol` may return `status=answering` (the configured provider is still answering): call it
  again with the same `protocol_id` and `question` until it returns the answer; do not explore client-side
  meanwhile. Calls with the same question join the running exploration, with or without `force=true`, and
  the first call after it ended receives its result (also with `force=true`), so repeat the call exactly as
  you made it. `force=true` starts a new exploration only when none is running or waiting to be collected.
- `analyze_protocol` may return `status=needs_heading_translation` for a protocol that is not in
  English (section roles are read from English heading words; only without a configured server LLM,
  which otherwise translates them itself). Resolve it before `research_protocol` on that protocol.
  Translate every entry of `headings` into English, keeping the numbering and translating only, then call
  `cache_heading_translations(protocol_id, translations={heading: English})` and `analyze_protocol`
  again. When its `reading_changed` is `true` (the translations changed), read the extraction
  batches again from `offset=0`. If you cannot translate, pass `translations={}`: every unrecognised
  section is then read.
- With `needs_client_extraction`, cache each batch via `cache_protocol_analysis(batch_offset=offset)`
  (also pass `reading=source.reading`, even when it is empty; only a non-English protocol requires it,
  and `READING_CONTEXT_CHANGED` means it was omitted there or the translations changed: call
  `analyze_protocol` and reread)
  so progress survives context compaction and a server restart (batches are kept in the database);
  skip offsets listed in `cached_batch_offsets`. Always continue at the `next_offset` the previous
  response returned and cache the last batch with its `batch_offset` too; then finish with
  `coverage_complete=true` (its `analysis` may be `{}` while cached batches remain to be merged).
  `INCOMPLETE_EXTRACTION` refuses a call in these cases, and its message says which. Without
  `coverage_complete=true` and without `batch_offset`, nothing is saved: cache each batch with its
  `batch_offset`. With an offset, it names an offset that was never read and cached (also for an empty
  final call when no batch was cached): read and cache it, follow `next_offset` to the end and repeat the call (a batch
  counts as far as it was served when you cached it, even if that offset was served again with another
  `max_chars`).
  Without cached batches, it refuses an empty `analysis` (or one with only `missing_information`) that
  would replace a saved analysis, for instance a repeated final call after the merge; the saved analysis
  is kept. To correct a saved analysis in one call, pass all of its items. A `batch_offset` that no
  `analyze_protocol` response returned is refused. `analyze_protocol(force_refresh=true)` at `offset=0`
  discards the study's cached batches with their answers (a server-side extraction's too): use it to start over,
  never to resume after context compaction. While a server-side extraction of the same PDF and reading is
  still running, `force_refresh=true` joins it instead of starting another.
- Section text is returned with runs of three or more spaces shortened to two (table columns keep a gap);
  copy quotes from it as they are. The comparison with the PDF treats a run of whitespace as one space and
  ignores whitespace right after a hyphen, so a word the PDF breaks after its hyphen at a line end
  (`ICD-O-` / `3`) may be written whole.
- A data source `value` is the name as the protocol writes it, with no words of your own (no added
  country or abbreviation). The name is matched by its words, in order, case and punctuation aside, in one
  of the fact's quotes. Up to three other words may stand between two words of the name, but not a `;`, a
  sentence end, or another word of the name. When your quotes leave the name out but the protocol's methods
  text writes it (on a page you cite, else the page nearest to any you cite), the server adds the naming
  passage as one more quote; it adds none to a fact that already has 8 quotes, nor a passage over 1000
  characters. `EVIDENCE_INVALID` names a value that is written nowhere outside the contents, references,
  background and checklists, or a fact with 8 quotes that leave the name out: correct the name, drop a
  quote, or cite the passage that names it.
- A quote has at least 8 characters (quote the whole line around a bare code or word) and a fact at most 8
  quotes (split the fact for more). A verbatim quote given on the wrong page is moved to the nearest page
  that holds it; a quote found on no page, or on two pages equally near, is refused.
- Leave `evidence.section` null unless you copy the exact label: the server fills a null label with
  the one section of the cited page that holds the quote and whose heading is on that page (null when
  none or several do). A label you give is checked, not replaced.
- In saved analyses and answers `evidence[].server` marks what the server did: `source_name` is a passage it
  added because it writes the data source's name (not support for the usage or definition; the comparison
  table lists its page apart), `page` a quote whose page it corrected. Leave `server` out of what you send: the server drops any `server`
  it did not set itself. If your client can run a cheaper subagent (Claude Code: `Agent` with
  model sonnet), delegate one study's batch reading and caching to it and keep only results in
  the main context.
- Quotes must be verbatim; pages are physical PDF pages; keep `usage` honest
  (`used`/`planned`/`candidate`/`unclear`). A planned source is never "used".
- `source_preference` (types, definition role, `mode=prefer` by default) ranks PDF evidence after
  screening. It is independent of the catalogue source-type filter and uses another vocabulary: types
  `claims`, `registry`, `ehr`, `drug_dispensing_prescription`, `other`, and roles `cohort`, `outcome`,
  `exposure`, `covariate`, `other`, `any` (the catalogue filter takes `claims`, `ehr`, `registry`,
  `others`; a block's `role` takes `outcome`, `condition`, `exposure`, `any`).
- Call `get_protocol_comparison`. If `selection_status=needs_selection`, show `screening_summary`
  and ask for `selected_study_ids` within `max_comparison_studies`.
- Present the comparison table, JSON paths, failures and missing information. A list over its schema limit
  (data sources 60, `key_notes` 20, answers 60) keeps entries taken in turn from every batch; how many were
  dropped is in `missing_information`.

## 5. Follow-up questions

`search_protocol_text`, `get_protocol_outline`, `read_protocol_text`, `research_protocol` and
`cache_protocol_answer` (pass the `reading` that `research_protocol` returned) work on archived PDFs by immutable `protocol_id` without EMA requests. If a
search, outline or read response reports another `source.reading` than `research_protocol` returned, the
heading translations changed: call `research_protocol` again and explore afresh. A
zero-hit search does not prove absence; inspect methods, outcome/exposure definitions and code-list
appendices.

## Response shapes and context budget

- `search_studies` returns compact rows by default (`detail=compact`): id, title, status, countries,
  source types, designs, conditions, score, analysis and protocol flags (`analysis_available`,
  `protocol_data_sources_status`, `protocol_available`, `protocol_listed`, `protocol_found`,
  `protocol_text_layer`), match basis and matched terms with their sources, rank features, observed
  exposures and `out_of_scope`. `detail=full` adds
  descriptions, provenance and the full query expansion.
- `compare_protocols` above the limit returns facets, candidates (when listed) and per-query
  `query_expansions` summaries only; no PDFs are fetched.
- `catalogue` inside search responses carries only `status`, `source_type_imports` and
  `browser_refresh_recommended`; call `catalogue_status` for directories and limits.
