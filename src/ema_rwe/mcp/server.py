import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..domain import CodeCandidate, Extraction, ProtocolAnswer, RWEError, SourcePreference
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
            "Evidence-backed search of Non-interventional EMA studies over a local catalogue; PDFs are fetched "
            "only for screened studies. Workflow: 1) plan_study_search to turn the question into English terms "
            "and codes; 2) compare_protocols with ALL query variants, role=outcome/condition/exposure when the "
            "question names one, darwin_only=false; 3) if needs_narrowing, ask the user for a source type "
            "(claims/ehr/registry/others) AND countries using facets, or let them pick study_ids from candidates, "
            "then rerun; 4) within max_screening_studies process every pending tool, cache all extractions and "
            "answers, then get_protocol_comparison. Never choose a subset silently, never call planned data "
            "sources used, cite PDF pages. Full procedure and field semantics: docs/mcp-workflow.md."
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
        """Refresh the cached EMA product-name/INN/ATC dictionary when drugs_need_refresh is true."""
        return await call("refresh_drug_dictionary", force)

    @server.tool()
    async def catalogue_status() -> dict:
        """Report local CSV coverage/freshness, import directories, imported source types and limits."""
        try:
            return service.catalogue_status()
        except RWEError as exc:
            return exc.as_dict()

    @server.tool()
    async def import_catalogue_csv(filename: str, column_map: dict[str, str] | None = None) -> dict:
        """Import one official Studies CSV by basename from studies/ or source_type/ (type in file name)."""
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
        role: Literal["any", "outcome", "condition", "exposure"] = "any",
        detail: Literal["compact", "full"] = "compact",
    ) -> dict:
        """Local catalogue search (no network). Multi-word queries must co-occur; role scopes the columns.

        Use darwin_only=false for all eligible studies. filters narrow by country, source type
        (claims/ehr/registry/others) and design. detail=full adds descriptions and provenance.
        """
        try:
            return service.search_studies(
                query, limit, darwin_only, status, analyzed_only, synonyms, codes, filters, role, detail
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
        source_preference: SourcePreference | None = None,
        role: Literal["any", "outcome", "condition", "exposure"] = "any",
        study_ids: list[str] | None = None,
    ) -> dict:
        """Screen the deduplicated union of all query variants; PDFs are fetched only within the limit.

        needs_narrowing returns facets and, when few enough, candidates: narrow via filters or pass the
        user's study_ids. source_preference ranks PDF evidence for a definition role after screening.
        """
        return await call(
            "compare_protocols",
            question,
            queries,
            filters,
            darwin_only,
            synonyms,
            codes,
            source_preference,
            role,
            study_ids,
        )

    @server.tool()
    async def get_protocol_comparison(
        comparison_id: str, selected_study_ids: list[str] | None = None
    ) -> dict:
        """Rebuild the comparison JSON/Markdown; if needs_selection, pass the user's selected_study_ids."""
        return await call("get_protocol_comparison", comparison_id, selected_study_ids)

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
        """Return the cached extraction, or paginated PDF sections to extract from (follow next_offset)."""
        return await call("analyze_protocol", study_id, force_refresh, offset, max_chars)

    @server.tool()
    async def cache_protocol_analysis(
        study_id: str, fingerprint: str, analysis: Extraction, coverage_complete: bool = False
    ) -> dict:
        """Save a caller extraction; fingerprint and verbatim quotes are verified before caching."""
        return await call("cache_protocol_analysis", study_id, fingerprint, analysis, coverage_complete)

    @server.tool()
    async def plan_study_search(question: str, use_llm: bool = False) -> dict:
        """Turn a question into English terms, code candidates and queries; does not search."""
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
        """Search one archived PDF (all sections) with synonym expansion; zero hits do not prove absence."""
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
        """Answer a question from one archived PDF: saved answer, bounded LLM run, or caller-driven hits."""
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
