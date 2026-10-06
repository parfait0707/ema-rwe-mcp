import json
import os
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ema_rwe.archive import ProtocolArchive
from ema_rwe.domain import Study
from ema_rwe.storage import Repository, import_csv


async def test_stdio_discovery_validation_and_local_search(settings, csv_file, pdf_bytes):
    import_csv(Repository(settings.db_path), csv_file)
    repo = Repository(settings.db_path)
    inbox = settings.db_path.parent / "imports" / "studies"
    inbox.mkdir(parents=True)
    shutil.copyfile(csv_file, inbox / "export-data.csv")
    for i in range(6):
        repo.upsert(
            Study(
                study_id=str(9000 + i),
                title="NarrowingExample",
                study_type="Non-interventional study",
                countries=["Japan" if i < 3 else "France"],
                data_source_types=["Claims"],
                study_designs=["Cohort"],
                source_url=f"https://catalogues.ema.europa.eu/study/{9000 + i}",
            )
        )
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
        init = await session.initialize()
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
            "cache_heading_translations",
            "compare_protocols",
            "get_protocol_comparison",
            "catalogue_status",
            "import_catalogue_csv",
        }
        # The caller procedure is readable without a checkout (it ships in the wheel)
        assert "ema-rwe://docs/mcp-workflow" in init.instructions
        resources = await session.list_resources()
        assert [str(r.uri) for r in resources.resources] == ["ema-rwe://docs/mcp-workflow"]
        workflow = await session.read_resource("ema-rwe://docs/mcp-workflow")
        assert workflow.contents[0].text.startswith("# MCP workflow for callers")
        catalogue = await session.call_tool("catalogue_status", {})
        assert json.loads(catalogue.content[0].text)["status"] == "current"
        assert json.loads(catalogue.content[0].text)["max_screening_studies"] == 5
        assert json.loads(catalogue.content[0].text)["max_comparison_studies"] == 5
        comparison_tool = next(t for t in tools.tools if t.name == "compare_protocols")
        assert "source_preference" in comparison_tool.inputSchema["properties"]
        collection_tool = next(t for t in tools.tools if t.name == "get_protocol_comparison")
        assert "selected_study_ids" in collection_tool.inputSchema["properties"]
        imported = await session.call_tool("import_catalogue_csv", {"filename": "export-data.csv"})
        assert json.loads(imported.content[0].text)["imported"] == 2
        outside = await session.call_tool("import_catalogue_csv", {"filename": "../export-data.csv"})
        assert json.loads(outside.content[0].text)["error"]["code"] == "INVALID_INPUT"
        search_tool = next(t for t in tools.tools if t.name == "search_studies")
        assert search_tool.inputSchema["properties"]["darwin_only"]["default"] is False
        assert search_tool.inputSchema["properties"]["codes"]["description"]
        for tool in (search_tool, comparison_tool):
            properties = tool.inputSchema["properties"]
            assert properties["match_scope"]["default"] == "concept"
            assert properties["analogous_terms"]["description"]
        compare_properties = comparison_tool.inputSchema["properties"]
        assert compare_properties["check_protocols"]["default"] == 0
        assert {"blocks", "category_terms"} <= set(compare_properties)
        assert "queries" not in comparison_tool.inputSchema.get("required", [])
        blocked = await session.call_tool(
            "compare_protocols",
            {
                "question": "q",
                "blocks": [
                    {"role": "exposure", "queries": ["zzqx"]},  # no match: stays offline (no PDF preparation)
                    {"role": "outcome", "queries": ["qqzz"], "category_terms": ["yyxx"]},
                ],
            },
        )
        body = json.loads(blocked.content[0].text)
        assert body["blocks"][1]["category_terms"] == ["yyxx"] and body["total_matches"] == 0
        assert body["network_requests"] == 0
        analogous = await session.call_tool(
            "search_studies",
            {
                "query": "zzqx disease",
                "match_scope": "analogous",
                "analogous_terms": [{"term": "opioid", "relation": "broader"}],
            },
        )
        body = json.loads(analogous.content[0].text)
        assert body["match_scope"] == "analogous" and body["results"]
        assert {r["match_basis"] for r in body["results"]} == {"analogous"}
        bad_relation = await session.call_tool(
            "search_studies",
            {
                "query": "x",
                "match_scope": "analogous",
                "analogous_terms": [{"term": "y", "relation": "synonym"}],
            },
        )
        assert bad_relation.isError
        result = await session.call_tool("search_studies", {"query": "opioid"})
        assert not result.isError
        body = json.loads(result.content[0].text)
        assert body["network_requests"] == 0
        assert body["results"][0]["data_source_types"] == ["EHR", "Claims"]
        narrow = await session.call_tool(
            "compare_protocols",
            {
                "question": "What definition?",
                "queries": ["NarrowingExample"],
                "source_preference": {"types": ["ehr"], "role": "outcome", "mode": "prefer"},
            },
        )
        body = json.loads(narrow.content[0].text)
        assert body["status"] == "needs_narrowing"
        assert body["total_matches"] == 6 and body["network_requests"] == 0
        assert body["source_preference"]["role"] == "outcome"
        invalid_preference = await session.call_tool(
            "compare_protocols",
            {
                "question": "What definition?",
                "queries": ["NarrowingExample"],
                "source_preference": {"types": ["claims"], "role": "made-up-role"},
            },
        )
        assert invalid_preference.isError
        filtered = await session.call_tool(
            "search_studies",
            {
                "query": "NarrowingExample",
                "darwin_only": False,
                "filters": {
                    "countries": ["Japan"],
                    "data_source_types": ["claims"],
                    "study_designs": ["cohort"],
                },
            },
        )
        assert json.loads(filtered.content[0].text)["total_matches"] == 3
        invalid_filter = await session.call_tool(
            "compare_protocols",
            {
                "question": "What definition?",
                "queries": ["NarrowingExample"],
                "filters": {"data_source_types": ["made-up-type"]},
            },
        )
        assert invalid_filter.isError
        missing_comparison = await session.call_tool("get_protocol_comparison", {"comparison_id": "../oops"})
        assert json.loads(missing_comparison.content[0].text)["error"]["code"] == "INVALID_INPUT"
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
        invalid_source = await session.call_tool(
            "cache_protocol_answer",
            {
                "protocol_id": pid,
                "question": "Source?",
                "answer": {
                    "source_assessments": [
                        {
                            "value": "Invented Database",
                            "types": ["claims"],
                            "role": "cohort",
                            "basis": "inferred",
                            "usage": "planned",
                            "requires_linkage": False,
                            "definition": "Patients defined using diagnoses",
                            "evidence": [
                                {
                                    "page": 1,
                                    "quote": "The study will use Example Primary Care Database (EPCD).",
                                }
                            ],
                        }
                    ]
                },
            },
        )
        assert json.loads(invalid_source.content[0].text)["error"]["code"] == "EVIDENCE_INVALID"
