"""Tool annotations and progress notifications as an MCP client sees them (spec v1.16)."""

import asyncio

from mcp.shared.memory import create_connected_server_and_client_session
from test_comparison import seed
from test_core import sample_analysis

from ema_rwe.domain import Extraction
from ema_rwe.mcp.server import ANNOTATIONS, create_server

READ_ONLY = {
    "catalogue_status",
    "search_studies",
    "plan_study_search",
    "list_local_protocols",
    "get_protocol_outline",
    "search_protocol_text",
    "read_protocol_text",
}


async def test_every_tool_is_annotated_and_the_context_is_not_an_argument(service):
    async with create_connected_server_and_client_session(create_server(service)._mcp_server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == set(ANNOTATIONS) and len(tools) == 18
    for name, tool in tools.items():
        hints = tool.annotations
        assert tool.title == hints.title == ANNOTATIONS[name][0]
        assert None not in (
            hints.readOnlyHint,
            hints.destructiveHint,
            hints.idempotentHint,
            hints.openWorldHint,
        )
        assert "ctx" not in tool.inputSchema["properties"]
        assert not (hints.readOnlyHint and hints.destructiveHint)
    assert {name for name, tool in tools.items() if tool.annotations.readOnlyHint} == READ_ONLY
    # The tools that only read local data reach nothing outside, except the optional LLM of plan_study_search
    assert {n for n in READ_ONLY if tools[n].annotations.openWorldHint} == {"plan_study_search"}
    assert {name for name, tool in tools.items() if tool.annotations.destructiveHint} == {
        "research_protocol",
        "cache_protocol_answer",
        "import_catalogue_csv",
        "refresh_drug_dictionary",
    }
    for fetching in ("get_study", "get_protocol", "compare_protocols", "analyze_protocol"):
        assert tools[fetching].annotations.openWorldHint


async def test_preparing_a_comparison_reports_each_study(service):
    seed(service.repo, 2)
    seen = []

    async def on_progress(done, total, message):
        seen.append((done, total, message))

    async with create_connected_server_and_client_session(create_server(service)._mcp_server) as client:
        result = await client.call_tool(
            "compare_protocols",
            {"question": "What design?", "queries": ["opioid"]},
            progress_callback=on_progress,
        )
        await asyncio.sleep(0.05)  # notifications are delivered beside the response
    assert not result.isError
    assert [(done, total) for done, total, _ in seen] == [(0, 2), (1, 2)]
    assert all("Retrieving the protocol of study" in message for _, _, message in seen)


async def test_a_call_without_a_progress_request_sends_none(service):
    seed(service.repo, 1)
    async with create_connected_server_and_client_session(create_server(service)._mcp_server) as client:
        result = await client.call_tool(
            "compare_protocols", {"question": "What design?", "queries": ["opioid"]}
        )
    assert not result.isError


async def test_server_side_extraction_reports_finished_batches(service, monkeypatch):
    service.settings.llm_base_url, service.settings.llm_model = "https://provider.example/v1", "test-model"
    service.settings.llm_wait_seconds = 5
    release = asyncio.Event()

    async def slow_extract(_settings, _chunks, progress=None, checkpoint=None):
        progress.update(done=1, total=3)
        await release.wait()
        progress.update(done=3, total=3)
        return Extraction(study_design=sample_analysis().study_design)

    monkeypatch.setattr("ema_rwe.extraction.extract_with_provider", slow_extract)
    seen = []

    async def on_progress(done, total, message):
        seen.append((done, total, message))
        release.set()

    done = await service.analyze_protocol("123", on_progress=on_progress)
    assert done["status"] == "analyzed"
    assert seen[0][:2] == (1, 3) and "1 of 3 batches" in seen[0][2]
