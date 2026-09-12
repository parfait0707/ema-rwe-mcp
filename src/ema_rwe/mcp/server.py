import logging
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..domain import CodeCandidate, Extraction, ProtocolAnswer, RWEError
from ..selection import SearchFilters
from ..service import Service


def create_server(service: Service | None = None):
    @asynccontextmanager
    async def lifespan(server):
        nonlocal service
        service = service or Service()
        try:
            yield {}
        finally:
            await service.close()

    server = FastMCP(
        "EMA RWE protocol search",
        lifespan=lifespan,
        instructions=(
            "Search non-interventional EMA studies using English keywords where possible. Search is local; "
            "unanalysed protocol data sources are explicitly empty/not_analyzed. Analyze relevant candidates. "
            "When analyze_protocol returns needs_client_extraction, read all section batches, extract according "
            "to analysis_schema, then cache_protocol_analysis. Repeat search to return evidence-backed database "
            "names. Never describe planned data sources as actually used. Cite protocol source URLs and pages."
            " Inspect query_expansion, refine synonyms, or use plan_study_search. PDFs have stable protocol_id. "
            "For follow-up questions use research_protocol, search_protocol_text, get_protocol_outline, "
            "read_protocol_text and cache_protocol_answer. These tools explore all archived text without EMA requests."
            " Translate clinical questions into English names, related terms and typed code candidates using "
            "plan_study_search. Search codes independently too; verify vocabulary, version and outcome/exposure "
            "role from methods and code-list appendices. Retrieval hints are not study definitions."
            " For medicine questions, check clinical.drugs.needs_refresh and call refresh_drug_dictionary if needed. "
            "Search both product names and INN/common names plus ATC codes. Translate unknown Japanese drug names "
            "into English synonyms, then rerun search for dictionary expansion. Do not equate combination products with single ingredients."
            " For each research question, use compare_protocols with ALL query variants to count the deduplicated candidate union. "
            "If needs_narrowing, ask the user for countries, source types (claims/registry/ehr/drug_dispensing_prescription) "
            "or study designs (case-control/cohort/cross-sectional/ecological/self-controlled). NEVER choose a top five from six or more. "
            "For 1..5, process ALL pending_tools, cache all extractions and answers, and call get_protocol_comparison. "
            "Present its comparison table and JSON paths, including failures. Use darwin_only=false unless specifically requested."
            " Check catalogue_status before research. If no CSV has been imported, or it is stale and local "
            "search has no candidates, use the caller's Playwright MCP in a visible user-initiated session to "
            "download one official Studies CSV into study_import_directory, call import_catalogue_csv, and retry all "
            "queries. Do not crawl /search pages, run background sync, or repeatedly download an unchanged export."
        ),
    )

    async def call(method, *args, **kwargs):
        try:
            result = await getattr(service, method)(*args, **kwargs)
            return result.model_dump() if hasattr(result, "model_dump") else result
        except RWEError as exc:
            return exc.as_dict()
        except OSError:
            return RWEError("CACHE_ERROR", "Local cache or filesystem operation failed.").as_dict()

    @server.tool()
    async def refresh_drug_dictionary(force: bool = False) -> dict:
        """Download/cache official EMA human product-name, INN/common-name and ATC mappings. Reuse for seven days.

        Search stays offline. Refresh once before medicine searches if clinical.drugs.needs_refresh is true.
        Covers EMA centralised medicines, not every country's brands. No API key required.
        """
        return await call("refresh_drug_dictionary", force)

    @server.tool()
    async def catalogue_status() -> dict:
        """Report local CSV snapshot coverage/freshness and the typed import directories. No network request."""
        try:
            return service.catalogue_status()
        except RWEError as exc:
            return exc.as_dict()

    @server.tool()
    async def import_catalogue_csv(filename: str, column_map: dict[str, str] | None = None) -> dict:
        """Validate/import one browser-downloaded Studies CSV from the configured studies directory.

        Accepts a basename, never an arbitrary path. A legacy file in the import root is also accepted.
        The official raw bytes and checksum are retained.
        """
        try:
            return service.import_catalogue_csv(filename, column_map)
        except RWEError as exc:
            return exc.as_dict()

    @server.tool()
    async def search_studies(
        query: Annotated[str, Field(max_length=2000)],
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
        darwin_only: bool = True,
        status: list[str] | None = None,
        analyzed_only: bool = False,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
        filters: SearchFilters | None = None,
    ) -> dict:
        """Local search. Always returns catalogue data_source_types and protocol_data_sources with status.

        darwin_only is separate from Non-interventional scope. Use false for all eligible EMA studies.
        Protocol names require analyze_protocol then cache_protocol_analysis on first use.
        analyzed_only restricts results to cached analyses; source timestamps indicate freshness.
        """
        try:
            return service.search_studies(
                query, limit, darwin_only, status, analyzed_only, synonyms, codes, filters
            )
        except RWEError as exc:
            return exc.as_dict()

    @server.tool()
    async def compare_protocols(
        question: str,
        queries: list[str],
        filters: SearchFilters | None = None,
        darwin_only: bool = False,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
    ) -> dict:
        """Count the union of ALL search variants. At >5 require user filters before downloading any PDF.

        At 1..5 save every latest available PDF and per-study draft JSON. Execute all returned pending_tools,
        then get_protocol_comparison to assemble completed JSON and comparison table. Never omit failures.
        Source/design filters use labelled catalogue metadata (or saved design analysis); unknowns do not match.
        """
        return await call("compare_protocols", question, queries, filters, darwin_only, synonyms, codes)

    @server.tool()
    async def get_protocol_comparison(comparison_id: str) -> dict:
        """Update all comparison JSON/Markdown files from validated saved analyses and question answers.

        Status is complete only when EVERY row has its PDF, current-fingerprint extraction and saved answer.
        """
        return await call("get_protocol_comparison", comparison_id)

    @server.tool()
    async def get_study(study_id: str, refresh: bool = False) -> dict:
        """Get metadata, verify Non-interventional study type, and retrieve catalogue Data source types."""
        return await call("get_study", study_id, refresh)

    @server.tool()
    async def get_protocol(
        study_id: str, version: str = "latest", download: bool = True, refresh: bool = False
    ) -> dict:
        """Select latest protocol from Study documents, with selection reason and retained local PDF ID."""
        return await call("get_protocol", study_id, version, download, refresh)

    @server.tool()
    async def analyze_protocol(
        study_id: str,
        force_refresh: bool = False,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int, Field(ge=6000, le=60000)] = 30000,
    ) -> dict:
        """Reuse analysis or extract via configured LLM. Without provider, return paginated evidence sections.

        Read all next_offset batches, then use cache_protocol_analysis to persist your structured extraction.
        force_refresh rechecks documents and PDF, including changed bytes at the same URL.
        """
        return await call("analyze_protocol", study_id, force_refresh, offset, max_chars)

    @server.tool()
    async def cache_protocol_analysis(
        study_id: str, fingerprint: str, analysis: Extraction, coverage_complete: bool = False
    ) -> dict:
        """Cache caller-extracted facts after all batches were reviewed. Verifies fingerprint and exact quotes.

        Data source names must appear verbatim in quotes. Set usage used/planned/candidate/unclear honestly.
        This is the API-key-free extraction route for Claude Code and Codex.
        """
        return await call("cache_protocol_analysis", study_id, fingerprint, analysis, coverage_complete)

    @server.tool()
    async def plan_study_search(question: str, use_llm: bool = False) -> dict:
        """Show auditable synonyms; optionally ask the configured LLM for multiple search formulations.

        Execute/refine the returned queries with search_studies. Does not itself search/download studies.
        """
        return await call("plan_study_search", question, use_llm)

    @server.tool()
    async def list_local_protocols(study_id: str) -> dict:
        """List retained local PDF versions and their immutable protocol_id. No network requests."""
        return await call("list_local_protocols", study_id)

    @server.tool()
    async def get_protocol_outline(protocol_id: str, offset: int = 0, limit: int = 100) -> dict:
        """Inspect all PDF section IDs, pages, roles, parents, neighbouring chapters and structural warnings."""
        return await call("get_protocol_outline", protocol_id, offset, limit)

    @server.tool()
    async def search_protocol_text(
        protocol_id: str,
        query: str,
        limit: int = 10,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
    ) -> dict:
        """Search the entire archived PDF with synonym expansion, including initially excluded sections.

        Results expose section_id, role, parent and neighbours. A zero-hit search does not prove absence.
        """
        return await call("search_protocol_text", protocol_id, query, limit, synonyms, codes)

    @server.tool()
    async def read_protocol_text(
        protocol_id: str,
        section_id: str | None = None,
        start_page: int = 1,
        end_page: int | None = None,
        offset: int = 0,
        max_chars: int = 12000,
    ) -> dict:
        """Read a section chunk or 1..5 physical pages by immutable PDF ID. Follow next_offset for full text."""
        return await call(
            "read_protocol_text", protocol_id, section_id, start_page, end_page, offset, max_chars
        )

    @server.tool()
    async def research_protocol(protocol_id: str, question: str, force: bool = False) -> dict:
        """Answer a follow-up question from a retained PDF. Reuse saved answer or run bounded LLM exploration.

        Without internal LLM, returns initial hits and instructions for caller-driven exploration and caching.
        Internal LLM can refine searches, inspect outline and read pages; its actions are recorded in trace.
        """
        return await call("research_protocol", protocol_id, question, force)

    @server.tool()
    async def cache_protocol_answer(protocol_id: str, question: str, answer: ProtocolAnswer) -> dict:
        """Validate and save a caller's question-specific answer with exact quotes/pages/sections."""
        return await call("cache_protocol_answer", protocol_id, question, answer)

    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
