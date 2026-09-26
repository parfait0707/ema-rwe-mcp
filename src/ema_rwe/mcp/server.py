import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..domain import AnalogousTerm, CodeCandidate, Extraction, ProtocolAnswer, RWEError, SourcePreference
from ..selection import SearchFilters
from ..service import Service

MatchScope = Annotated[
    Literal["concept", "analogous"],
    Field(description="analogous searches only clinically analogous concepts (the zero-hit fallback)"),
]
AnalogousTerms = Annotated[
    list[AnalogousTerm] | None,
    Field(description="Caller-proposed analogous concepts {term, relation: broader/sibling/associated}"),
]


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
            "only for screened studies. Workflow: 1) plan_study_search; no disease dictionary ships, so on "
            "needs_client_translation generate English terms, synonyms, codes and analogous_terms yourself, "
            "exploring synonyms based on ICD-10 as client_expansion instructs; 2) compare_protocols with ALL query variants, role=outcome/condition/exposure when the "
            "question names one, darwin_only=false; 3) if needs_narrowing, ask the user for a source type "
            "(claims/ehr/registry/others) AND countries using facets, or let them pick study_ids from candidates, "
            "then rerun; 4) within max_screening_studies process every pending tool, cache all extractions and "
            "answers, then get_protocol_comparison. Zero hits return analogous_fallback: offer it and rerun with "
            "match_scope=analogous, labelling results as the analogous concept. "
            "Never choose a subset silently, never call planned data "
            "sources used, cite PDF pages. When analyze_protocol returns needs_client_extraction and your "
            "client can run a cheaper subagent (e.g. Claude Code Agent with model sonnet), delegate that "
            "study's batch reading and caching to it and keep only cached results in the main context. "
            "Full procedure and field semantics: docs/mcp-workflow.md."
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
    async def import_catalogue_csv(
        filename: str,
        column_map: Annotated[
            dict[str, str] | None,
            Field(description="Internal field name -> exact CSV header, for renamed export columns"),
        ] = None,
    ) -> dict:
        """Import one official Studies CSV by basename from studies/ or source_type/ (type in file name)."""
        try:
            return service.import_catalogue_csv(filename, column_map)
        except RWEError as exc:
            return exc.as_dict()

    @server.tool()
    async def search_studies(
        query: Annotated[str, Field(max_length=2000)],
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
        darwin_only: bool = False,
        status: list[str] | None = None,
        analyzed_only: bool = False,
        synonyms: Annotated[
            list[str] | None, Field(description="Extra English phrases OR-ed with the query")
        ] = None,
        codes: Annotated[
            list[CodeCandidate] | None,
            Field(description="Typed code hints {system, code}; matched as whole tokens, never re-mapped"),
        ] = None,
        filters: Annotated[
            SearchFilters | None,
            Field(
                description="Narrow by countries, data_source_types (claims/ehr/registry/others), study_designs"
            ),
        ] = None,
        role: Literal["any", "outcome", "condition", "exposure"] = "any",
        detail: Literal["compact", "full"] = "compact",
        match_scope: MatchScope = "concept",
        analogous_terms: AnalogousTerms = None,
    ) -> dict:
        """Local catalogue search (no network). Multi-word queries must co-occur; role scopes the columns.
        Rows carry match_basis, matched_terms and matched_term_sources; zero hits return analogous_fallback.

        Use darwin_only=false for all eligible studies. filters narrow by country, source type
        (claims/ehr/registry/others) and design. detail=full adds descriptions and provenance.
        """
        try:
            return service.search_studies(
                query,
                limit,
                darwin_only,
                status,
                analyzed_only,
                synonyms,
                codes,
                filters,
                role,
                detail,
                match_scope,
                analogous_terms,
            )
        except RWEError as exc:
            return exc.as_dict()

    @server.tool()
    async def compare_protocols(
        question: str,
        queries: list[str],
        filters: Annotated[
            SearchFilters | None,
            Field(
                description="Narrow by countries, data_source_types (claims/ehr/registry/others), study_designs"
            ),
        ] = None,
        darwin_only: bool = False,
        synonyms: Annotated[
            list[str] | None, Field(description="Extra English phrases OR-ed with the query")
        ] = None,
        codes: Annotated[
            list[CodeCandidate] | None,
            Field(description="Typed code hints {system, code}; matched as whole tokens, never re-mapped"),
        ] = None,
        source_preference: SourcePreference | None = None,
        role: Literal["any", "outcome", "condition", "exposure"] = "any",
        study_ids: list[str] | None = None,
        match_scope: MatchScope = "concept",
        analogous_terms: AnalogousTerms = None,
    ) -> dict:
        """Screen the deduplicated union of all query variants; PDFs are fetched only within the limit.

        needs_narrowing returns facets and, when few enough, candidates: narrow via filters or pass the
        user's study_ids. source_preference ranks PDF evidence for a definition role after screening.
        Zero hits return analogous_fallback; rerun with match_scope=analogous to screen those studies.
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
            match_scope,
            analogous_terms,
        )

    @server.tool()
    async def get_protocol_comparison(
        comparison_id: str,
        selected_study_ids: list[str] | None = None,
        detail: Literal["compact", "full"] = "compact",
    ) -> dict:
        """Rebuild the comparison JSON/Markdown (compact rows; detail=full adds analysis/answer bodies); if needs_selection, pass selected_study_ids."""
        return await call("get_protocol_comparison", comparison_id, selected_study_ids, detail)

    @server.tool()
    async def get_study(
        study_id: str,
        refresh: Annotated[
            bool, Field(description="Re-fetch the EMA pages instead of the cached copy")
        ] = False,
    ) -> dict:
        """Get metadata, verify Non-interventional study type, and retrieve catalogue Data source types."""
        return await call("get_study", study_id, refresh)

    @server.tool()
    async def get_protocol(
        study_id: str,
        version: Annotated[
            str, Field(description="'latest' or an exact Study-documents version label")
        ] = "latest",
        download: bool = True,
        refresh: Annotated[
            bool, Field(description="Re-fetch the EMA pages instead of the cached copy")
        ] = False,
    ) -> dict:
        """Select latest protocol from Study documents, with selection reason and retained local PDF ID."""
        return await call("get_protocol", study_id, version, download, refresh)

    @server.tool()
    async def analyze_protocol(
        study_id: str,
        force_refresh: Annotated[
            bool, Field(description="Re-fetch the protocol and discard the cached analysis")
        ] = False,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int, Field(ge=6000, le=150000)] = 30000,
        detail: Literal["summary", "full"] = "summary",
    ) -> dict:
        """Cached extraction summary (detail=full for the whole extraction); status=extracting means call again later; else paginated sections to extract (follow next_offset)."""
        return await call("analyze_protocol", study_id, force_refresh, offset, max_chars, detail)

    @server.tool()
    async def cache_protocol_analysis(
        study_id: str,
        fingerprint: Annotated[
            str, Field(description="fingerprint returned by analyze_protocol; stale batches are rejected")
        ],
        analysis: Extraction,
        coverage_complete: bool = False,
        batch_offset: int | None = None,
    ) -> dict:
        """Save a caller extraction (verbatim quotes verified). Pass batch_offset per batch; finish with coverage_complete=true."""
        return await call(
            "cache_protocol_analysis", study_id, fingerprint, analysis, coverage_complete, batch_offset
        )

    @server.tool()
    async def plan_study_search(
        question: str,
        use_llm: Annotated[
            bool,
            Field(
                description="Also ask the configured LLM for terms and codes; false uses the dictionaries only"
            ),
        ] = False,
    ) -> dict:
        """Turn a question into English terms, code candidates and queries; does not search."""
        return await call("plan_study_search", question, use_llm)

    @server.tool()
    async def list_local_protocols(study_id: str) -> dict:
        """List retained local PDF versions and their immutable protocol_id. No network requests."""
        return await call("list_local_protocols", study_id)

    @server.tool()
    async def get_protocol_outline(
        protocol_id: str, offset: int = 0, limit: int = 100, detail: Literal["compact", "full"] = "compact"
    ) -> dict:
        """List PDF section IDs, pages, titles and roles; detail=full adds parents, neighbours and structural warnings."""
        return await call("get_protocol_outline", protocol_id, offset, limit, detail)

    @server.tool()
    async def search_protocol_text(
        protocol_id: str,
        query: str,
        limit: int = 10,
        synonyms: Annotated[
            list[str] | None, Field(description="Extra English phrases OR-ed with the query")
        ] = None,
        codes: Annotated[
            list[CodeCandidate] | None,
            Field(description="Typed code hints {system, code}; matched as whole tokens, never re-mapped"),
        ] = None,
        max_chars: Annotated[int, Field(ge=1000)] | None = None,
    ) -> dict:
        """Search one archived PDF (all sections) with synonym expansion; hit text is included up to max_chars in rank order. Zero hits do not prove absence."""
        return await call("search_protocol_text", protocol_id, query, limit, synonyms, codes, max_chars)

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

    for tool in server._tool_manager._tools.values():
        tool.parameters = strip_titles(tool.parameters)
    return server


def strip_titles(node):
    """Drop Pydantic's auto-generated "title" keys from advertised tool schemas (about 18% of their size)."""
    if isinstance(node, dict):
        node.pop("title", None)
        for value in node.values():
            strip_titles(value)
    elif isinstance(node, list):
        for value in node:
            strip_titles(value)
    return node


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
