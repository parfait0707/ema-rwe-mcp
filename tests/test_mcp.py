import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ema_rwe.archive import ProtocolArchive
from ema_rwe.storage import Repository, import_csv


async def test_stdio_discovery_validation_and_local_search(settings, csv_file, pdf_bytes):
    import_csv(Repository(settings.db_path), csv_file)
    pid = ProtocolArchive(settings.db_path.parent / "protocols").save(
        "123",
        pdf_bytes,
        {"document_url": "https://catalogues.ema.europa.eu/system/files/protocol.pdf", "version": "2"},
    )["protocol_id"]
    env = dict(
        os.environ,
        EMA_DB_PATH=str(settings.db_path),
        EMA_CACHE_DIR=str(settings.cache_dir),
        LLM_BASE_URL="",
        LLM_MODEL="",
    )
    params = StdioServerParameters(command=sys.executable, args=["-m", "ema_rwe.mcp.server"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {
            "refresh_drug_dictionary",
            "search_studies",
            "get_study",
            "get_protocol",
            "analyze_protocol",
            "cache_protocol_analysis",
            "plan_study_search",
            "list_local_protocols",
            "get_protocol_outline",
            "search_protocol_text",
            "read_protocol_text",
            "research_protocol",
            "cache_protocol_answer",
        }
        result = await session.call_tool("search_studies", {"query": "opioid"})
        assert not result.isError
        body = json.loads(result.content[0].text)
        assert body["network_requests"] == 0
        assert body["results"][0]["data_source_types"] == ["EHR", "Claims"]
        invalid = await session.call_tool("search_studies", {"query": "opioid", "limit": 21})
        assert invalid.isError
        error = await session.call_tool("get_study", {"study_id": "../oops"})
        assert json.loads(error.content[0].text)["error"]["code"] == "INVALID_INPUT"
        hits = await session.call_tool("search_protocol_text", {"protocol_id": pid, "query": "傾向スコア"})
        found = json.loads(hits.content[0].text)
        assert found["results"] and found["network_requests"] == 0
        codes = await session.call_tool(
            "search_protocol_text",
            {
                "protocol_id": pid,
                "query": "unmatched",
                "codes": [{"system": "ICD-10", "code": "E11"}],
            },
        )
        assert "E11" in json.loads(codes.content[0].text)["results"][0]["matched_terms"]
        read = await session.call_tool(
            "read_protocol_text", {"protocol_id": pid, "section_id": found["results"][0]["section_id"]}
        )
        assert "propensity score" in json.loads(read.content[0].text)["text"]
        saved = await session.call_tool(
            "cache_protocol_answer",
            {
                "protocol_id": pid,
                "question": "Analysis?",
                "answer": {
                    "answers": [
                        {
                            "value": "Propensity score weighting and Cox regression",
                            "evidence": [
                                {
                                    "page": 1,
                                    "quote": "We will use propensity score weighting and Cox regression.",
                                }
                            ],
                        }
                    ]
                },
            },
        )
        assert json.loads(saved.content[0].text)["status"] == "answered"
        cached = await session.call_tool("research_protocol", {"protocol_id": pid, "question": "Analysis?"})
        assert json.loads(cached.content[0].text)["cached"]
