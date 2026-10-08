# Changelog

## Unreleased

- Medicine names relate as equivalent, broader, narrower or different (spec v1.9): a catalogue name
  without the strain or type the query gives (`influenza, live attenuated` for an H5N1 vaccine) becomes a
  category term, not a synonym. Class expansions list the EMA records left out because the catalogue names
  their code otherwise (`omitted_members`, at most 30, and `omitted_members_total`): 86 across all
  classes, mostly true members named differently from their ATC substance (recombinant factor VIII
  INNs, vaccine products), some EMA code errors; the caller adds those that belong to a requested class.
  A current product name without its `(previously ...)` note (Icandra, GoResp Digihaler, Vantavo) now
  resolves like the full name; a name shared by different ingredient sets is not used.
- Heading readings are consistent across server processes (spec v1.8): the language and heading
  translations of each PDF move from `<protocol_id>.headings` into the user database (`protocol_readings`;
  an existing `.headings` is adopted once). A translation change retires the PDF's analysis in the same
  transaction, and an analysis is saved only while the PDF is still read the way it was extracted
  (`source.reading`, checked in one write transaction; otherwise `READING_CONTEXT_CHANGED`), so another
  process sharing the database cannot save or serve an analysis read under old translations. Callers pass
  `reading` to `cache_protocol_analysis` (CLI `--reading`); English protocols may omit it, and analyses
  saved before readings were recorded stay cached for them.
  Comparisons check the reading too; question answers are explored and cached under one reading
  (`research_protocol` returns it, `cache_protocol_answer` takes it, CLI `cache-answer --reading`); a first
  language check never overwrites translations saved meanwhile.
- Catalogue snapshots (code/data versioning plan, stage 1): `scripts/build_catalogue_snapshot.py <YYYYMMDD>`
  builds a database from exactly the four exports of one refresh (full Studies plus claims, ehr and
  registry; a missing or duplicated kind stops it), keeps the committed database's protocol observations,
  compacts and checks it, and writes the database, its gzip and `manifest.json` (schema, SHA-256 before and
  after compression, export files and rows) for a `data-YYYYMMDD` GitHub release. `ema_rwe.snapshot.verify_snapshot`
  checks a downloaded snapshot against its manifest. Servers do not fetch snapshots yet (stage 2).
- Medicine names correspond by whole words, never by letters (spec v1.7): an EMA dictionary code joins the
  catalogue's name for it only when the words of one name, salts aside, contain the other's, and numbers
  both names give (types, valency) agree. A prodrug or conjugate named with a prefix stays another medicine
  (fosaprepitant no longer adds aprepitant; 29 such latent pairs, e.g. omeprazole and esomeprazole, are
  separated), while qualifiers and word order no longer block a join (human insulin (rDNA), HPV and
  hexavalent vaccines). Names split into ingredients only at separators outside brackets. Of 5,404
  catalogue and EMA names, 30 expansions change: 17 better, 12 neutral, 1 worse (the antiemetic class
  A04 no longer lists fosaprepitant, whose code the catalogue names aprepitant).
- `catalogue_status` judges freshness per export (spec v1.7): `status=current` needs the full Studies
  export and every imported source-type export within the TTL, so a fresh registry export no longer makes
  an old full export look current. `exports` gives each export's file, date and status,
  `missing_source_type_exports` the type exports never imported, and `snapshot_aligned=false` (exports of
  different dates) recommends a refresh. `age_seconds` is now the full export's age.
- Re-saving different heading translations also cancels the study's server-side extraction, running or
  finished but not collected, so a result read under the old translations is never saved or returned.
- The MCP instructions state the rules (workflow resource, asking for source type and countries above
  the screening limit, no silent subset, every pending tool, planned sources are not used, verbatim quotes
  with physical pages) within their first 512 characters, before the procedure.

## 0.5.4 (2026-10-06)

- Non-English protocols (spec v1.6): section roles are read from English translations of their headings, so
  background, reference, administrative and contents chapters are no longer read; sections whose
  translated heading names no role are still read, since the English signal words cannot read the text
  (15 local non-English protocols: 1.63M -> 1.54M characters read, no required passage lost). `analyze_protocol` checks the language once per PDF; the
  configured LLM translates the headings, otherwise it returns `status=needs_heading_translation` and the
  caller saves translations with the new tool `cache_heading_translations` (`{}` keeps reading every
  unrecognised section). Translations are stored beside the archived PDF (`.headings`) and also apply to
  the exploration tools; re-saving different translations discards that PDF's analysis and held batches.
  The CLI saves translations with `ema-rwe cache-headings <protocol_id> <file.json>`.
  Saved translations are part of the research-answer cache key, so an answer cached before translation
  is not reused afterwards; callers resolve `needs_heading_translation` before `research_protocol`.
- Release audit: the CLI gains `ema-rwe cache-answer <protocol_id> <question> <file.json>` (the CLI side of
  `cache_protocol_answer`). The `analyze_protocol` description and the comparison instruction name
  `needs_heading_translation` and `extracting`; `cache_heading_translations` returns `study_id`; a failed
  server-side heading translation says so; `refresh_drug_dictionary` notes that `salt_word_candidates`
  are for maintainers. Documentation drift found against the code was corrected (README_DEV, docs, spec
  v1.5/v1.6, `.env.example`, AGENTS.md).
- `ema-rwe import-all` and `ema-rwe merge-observations`, the commands that build the bundled database,
  end with `VACUUM` (the result reports `bytes_before`/`bytes_after`). On a copy of the current bundle:
  45.8 MB -> 43.1 MB, gzip 17.1 MB -> 12.1 MB. The committed database is compacted at its next rebuild.
- Salt words: choline, diolamine, meglumine, semisodium and anhydrous are counter-ion/hydrate words (a base
  name now finds e.g. Yselty, Orepaxam and Vyndaqel); words reviewed as other substances or product lines
  (disoproxil, pegol, ...) are listed separately. `refresh_drug_dictionary` reports unreviewed candidates
  as `salt_word_candidates`; additions stay a code-review decision.
- Protocol structure detection is generalised (spec v1.5, parser `structural-v24`; cached analyses and
  research answers are re-extracted):
  - Section roles drive three separate decisions. Contents, background, administrative, reference and
    checklist sections are not read; ethics, safety-reporting and dissemination chapters (`conduct`) and
    amendment chapters (`amendments`) are read, and the extraction prompt keeps superseded amendment
    conditions apart from current ones. A method quote is rejected only when it occurs solely in
    references, contents, background or checklist text. Each section records its `role_basis`.
    Unknown sections are read once the protocol body has started unless their chapter is background,
    administrative or references, and always when the English role words recognise few of a document's
    headings (another language or template). A section with few citation marks (years, et al, DOIs) is
    not treated as a reference list.
  - Section numbers are parsed alike for arabic and roman numbering (`II.4`); a single-level number is a
    heading when its title is in capitals or is an EMA PASS template chapter title (`13 References`).
    A first level above 30 or a jump far past the current chapter is a code or value, not a section.
    Unnumbered all-caps lines naming a method topic are headings; a number followed by a lower-case word,
    and an appendix line that reads as a sentence, are not.
  - Without bookmarks, a contents page marks where sections start (never chapters or roles, which can
    drift) once its printed-to-physical page offset is confirmed by at least three entries; otherwise a
    larger or bold line with the next chapter number is a chapter heading. Outline titles start sections, and text above them stays in the previous chapter.
    List-of-tables/figures bookmarks are ignored as navigation.
  - The ENCePP checklist range runs from its title or heading to the next appendix or chapter, and only
    when questions and Yes/No/N/A answer columns follow; a title merely listed in contents is kept as text
    with a warning.
  - `scripts/structure_eval.py` measures structure detection reproducibly (PDF hashes, parser version,
    denominators, a template-family dev/eval split and independent eval labels).
- Prompts no longer encode examples from individual verified studies: the stated-count rule covers any
  list (outcomes, endpoints, cohorts, analyses, definitions) instead of "two types of outcomes", the
  database rule asks for every database named for the study with its supported usage (an assessed one is
  a candidate) instead of naming abstract/feasibility sections, and the linkage rule drops the
  hospital-network example. The `COUNT_CLAIM` audit (it matched 2 of 433 local protocols) is removed.
  Translation instructions say non-English instead of Japanese; checklist and category examples are
  marked as illustrations. Cached analyses and research answers are re-extracted (fingerprint and
  explorer version change).
- Fixes from an external review:
  - A page without a text heading that starts a new bookmark chapter no longer inherits the previous
    page's heading (a methods chapter after `BACKGROUND` was read as background and its quotes rejected
    with `EVIDENCE_WRONG_SECTION`). A top-level bookmark chapter with a known role sets the parent
    context; appendix references are matched against the whole bookmark path, and `annexes` counts as
    an appendix title.
  - A quote must have 8 characters once whitespace is collapsed: a blank quote normalized to an empty
    string and was "found" on every page.
  - Server-side extraction tasks are keyed by study and fingerprint: a protocol that changes during
    extraction cancels the old task instead of returning or saving the old PDF's result.
  - Facts beyond the schema limits when batches are merged, and `missing_information` overflow, are
    reported with counts in `missing_information` instead of being dropped silently.
  - A catalogue ATC name matches an EMA medicine only when every ingredient on each side corresponds to
    one on the other (`metformin` no longer matches empagliflozin + metformin).
  - The first copy of the bundled data goes through a temporary file and a rename, so an interrupted or
    concurrent first start cannot leave a truncated database that later starts skip.

## 0.5.3 (2026-10-03)

- A `get_study` refresh keeps an exported study's source-type tags (an empty set means `others`); only a
  study known solely from its detail page takes the page's F8.7 values.
- The CLI `search` searches every study by default, as MCP does (`--darwin-only` restricts it;
  `--all-studies` is accepted and ignored). The CLI takes the MCP arguments it lacked: `analyze --detail`,
  `cache-analysis --batch-offset`, `comparison --detail`, `outline --limit --detail`,
  `pdf-search --limit --max-chars`, `pdf-read --max-chars`.

## 0.5.2 (2026-10-03)

- The caller procedure `docs/mcp-workflow.md` ships in the wheel and is served as the MCP resource
  `ema-rwe://docs/mcp-workflow`; the server `instructions` point to it, so clients installed with
  `uvx` can read it without a checkout.
- Tool schemas state the ranges the server already enforced (`get_protocol_outline`,
  `search_protocol_text`, `read_protocol_text`); `compare_protocols`/`search_studies` descriptions,
  the `needs_narrowing` next action and CLI help match the actual ranking, narrowing and defaults.
- Documentation brought in line with the implementation: medicine matching and expansion, backfill,
  concept blocks, ranking, narrowing (`facets.conditions` is not a filter), analogous reruns, protocol
  version selection, cache TTLs, server-side extraction conditions, `.env.example` LLM defaults (empty
  integer values failed at startup) and the Codex sample (Playwright disabled until configured).

## 0.5.1 (2026-10-03)

- The backfill also records, from a PASS information table's medicine fields, the catalogue's own class
  names and the ATC codes written there (a code split across a line is rejoined; 'code (name)' fields
  give each code to the name after it); a code with no known name is kept under the catalogue's name for
  it or as the code. Codes in the protocol body are not used: they also define exclusions, outcomes and
  covariates. Recorded codes are indexed with the medicine names.
- Names followed by receptor/inhibitor/antagonist/agonist/block… ('angiotensin II receptor blockers')
  are not taken as that medicine; text matches skip a study's acronym ('SONATA study') and measured
  substances ('fractional exhaled nitric oxide'); '™'/'℠' no longer stick to a name ('VIZAMYL™').
- Observations carry `exposure_rules` (per-source extraction version); a bundle extracted with newer
  rules replaces that source's entries in existing user databases. The bundled database now fills 215
  of the 712 studies without medicines (76 from PASS tables).

## 0.5.0 (2026-10-02)

- Facts learnt by retrieving a protocol (whether it exists, its text layer, medicines filled from it)
  are kept in a `protocol_observations` table apart from the catalogue, so re-imports and bundle
  refreshes no longer erase them. A study whose retrieval found a protocol is not held back for an
  export without a protocol location; one whose retrieval found none ranks last. Database schema 6.
- `ema-rwe backfill-protocols` fills the medicines of studies whose export lists none: names written in
  their title, description or objective, and the 'Active substance' / 'Medicinal product' fields of
  their protocol's PASS information table (one study at a time, rate-limited, resumable, stops at a
  rate limit or outage; `--reextract` re-reads kept protocols offline; the one exception to fetching
  protocols for selected studies only). The bundled database ships the backfill (names, ATC codes and
  pages only: 209 of the 712 studies without medicines) and fills missing fields of existing user
  observations. `ema-rwe merge-observations` builds the bundle from a backfill run.
- A permanent HTTP error (4xx other than 408/429) is reported as `EMA_HTTP_ERROR`, not
  `EMA_UNAVAILABLE`.
- Analogous concepts are screened and ranked like concept candidates, excluding studies that match the
  requested concept in any column and skipping blank terms; matched terms are reported as written, and
  those built from an ATC class are labelled `catalogue_atc`; the broader concept falls back to the 3rd
  ATC level.
- Salted ingredient names in the EMA dictionary (dabigatran etexilate, enoxaparin sodium) are found by
  their base name under whole-term matching; the salt words are those observed in the data.

## 0.4.0 (2026-10-02)

- Medicine search is keyed on names (spec v1.0, v1.1). `compare_protocols` resolves each medicine query
  through the catalogue's own `(ATC code) name` entries and the EMA medicines dictionary: an ingredient
  adds its 4th-level ATC class as category terms, a class (catalogue class name or 3rd/4th-level code)
  adds the names of its members, and the response reports what was added in `medicine_expansion`. An EMA
  record's ATC code is never a catalogue search key. Each query is matched as one whole medicine name
  (trailing salt words aside); a combination resolves only to the whole ingredient set.
- Ranking puts studies whose export lists no protocol (`protocol_listed=false`) after listed ones, and a
  study whose downloaded protocol has no text layer (`protocol_text_layer=none`) last. No candidate is
  removed. The bundled catalogue DB records `protocol_listed`.
- A medicine with no study offers its ATC class and the class members the catalogue records as analogous
  concepts when the caller passes its 5th-level code.
- Fix: retrieving a protocol no longer erases the catalogue's medicines, conditions, outcomes and
  objective from the study (the detail pages do not carry them).
- The caller instructions ask for EU and US product names, abbreviations and spelling variants.
- Database schema 5 (the two new study fields). 0.4.0 upgrades a version-4 database in place; an older
  server sharing the same data directory then stops with "Database schema is newer than this server
  supports" and must be updated.

## 0.3.3 (2026-10-01)

- `NOTICE` states the source of the bundled EMA data (catalogue DB, medicines dictionary, a test HTML
  excerpt) with © EMA, the access month and the applicable EMA legal notices, and that the MIT License
  covers the source code only. It ships in the wheel (`dist-info/licenses/NOTICE`) and the sdist.
- The repository is renamed to `ema-rwe-mcp`; the old `rwd-catalogue-mcp` URLs redirect.
- The gold-set pools (`tests/fixtures/gold/pool/`), which quote catalogue records, are no longer
  tracked.

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
