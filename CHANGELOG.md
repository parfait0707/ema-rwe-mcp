# Changelog

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
