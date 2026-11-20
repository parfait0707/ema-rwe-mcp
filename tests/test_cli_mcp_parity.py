"""The CLI and the MCP tools take the same arguments (AGENTS.md: keep CLI, MCP schemas and documentation
consistent). The tables below say how the two name things; a parameter added to one side without the other,
a different default or choice list, or a bound of a top-level parameter the core does not enforce fails here.
Bounds inside structured parameters (filters, blocks, analysis) are not compared: both routes build those
with the same pydantic models."""

import argparse

import pytest
from test_core import sample_analysis

from ema_rwe.cli import parser
from ema_rwe.domain import Extraction, ProtocolAnswer, RWEError
from ema_rwe.mcp.server import create_server

# CLI subcommand -> MCP tool.
PAIRS = {
    "refresh-drugs": "refresh_drug_dictionary",
    "catalogue-status": "catalogue_status",
    "import-download": "import_catalogue_csv",
    "search": "search_studies",
    "compare": "compare_protocols",
    "comparison": "get_protocol_comparison",
    "plan-search": "plan_study_search",
    "local-protocols": "list_local_protocols",
    "outline": "get_protocol_outline",
    "pdf-search": "search_protocol_text",
    "pdf-read": "read_protocol_text",
    "ask": "research_protocol",
    "study": "get_study",
    "protocol": "get_protocol",
    "analyze": "analyze_protocol",
    "cache-analysis": "cache_protocol_analysis",
    "cache-answer": "cache_protocol_answer",
    "cache-headings": "cache_heading_translations",
}
# Maintenance commands a user runs in a terminal; they have no tool (docs: README_DEV.md).
CLI_ONLY_COMMANDS = {
    "import-csv",
    "import-all",
    "backfill-protocols",
    "merge-observations",
    "cleanup-cache",
    "update-catalogue",  # the server does this by itself at start (spec v1.18)
}
# CLI destination -> MCP parameter, where the names differ (repeatable flags are singular, files are 'input').
RENAMED = {
    "search": {"synonym": "synonyms", "code": "codes", "analogous": "analogous_terms"},
    "compare": {
        "query": "queries",
        "synonym": "synonyms",
        "code": "codes",
        "analogous": "analogous_terms",
        "category_term": "category_terms",
    },
    "comparison": {"study_id": "selected_study_ids"},
    "plan-search": {"llm": "use_llm"},
    "pdf-search": {"synonym": "synonyms", "code": "codes"},
    "protocol": {"no_download": "download"},
    "analyze": {"refresh": "force_refresh"},
    "cache-analysis": {"input": "analysis"},
    "cache-answer": {"input": "answer"},
    "cache-headings": {"input": "translations"},
}
# Several CLI flags that build one structured MCP parameter: flag -> field of that parameter.
GROUPED = {
    "filters": {"country": "countries", "source_type": "data_source_types", "study_design": "study_designs"},
    "source_preference": {"prefer_source_type": "types", "source_role": "role", "source_mode": "mode"},
}
# A CLI flag whose value is the negation of the MCP parameter.
NEGATED = {("protocol", "no_download")}
# CLI arguments without an MCP parameter: a hidden flag kept so that old command lines still parse.
CLI_ONLY_ARGUMENTS = {"search": {"all_studies"}}


@pytest.fixture(scope="module")
def commands():
    subparsers = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
    return {
        name: {a.dest: a for a in sub._actions if a.dest != "help"}
        for name, sub in subparsers.choices.items()
    }


@pytest.fixture
def tools(service):
    return {name: tool.parameters for name, tool in create_server(service)._tool_manager._tools.items()}


def resolve(schema: dict, root: dict) -> dict:
    """The schema a property stands for: through $ref, and the non-null branch of an optional one."""
    while True:
        if "$ref" in schema:
            schema = root["$defs"][schema["$ref"].rsplit("/", 1)[1]]
        elif "anyOf" in schema:
            schema = {**schema, **next(s for s in schema["anyOf"] if s.get("type") != "null")}
            schema.pop("anyOf")
        else:
            return schema


def mapping(command: str, arguments: dict, properties: dict) -> dict[str, tuple[str, str | None]]:
    """CLI destination -> (MCP parameter, field inside it or None)."""
    result = {}
    for dest in arguments:
        if dest in CLI_ONLY_ARGUMENTS.get(command, ()):
            continue
        group = next((p for p, flags in GROUPED.items() if dest in flags and p in properties), None)
        result[dest] = (
            (group, GROUPED[group][dest]) if group else (RENAMED.get(command, {}).get(dest, dest), None)
        )
    return result


def test_every_tool_has_a_command_and_every_command_is_accounted_for(commands, tools):
    assert set(PAIRS.values()) == set(tools) and len(set(PAIRS.values())) == len(PAIRS)
    assert set(commands) == set(PAIRS) | CLI_ONLY_COMMANDS


def test_commands_and_tools_take_the_same_arguments(commands, tools):
    for command, tool in PAIRS.items():
        properties = tools[tool]["properties"]
        mapped = mapping(command, commands[command], properties)
        assert {parameter for parameter, _ in mapped.values()} == set(properties), (command, tool)
        for parameter in GROUPED:
            if parameter in properties:  # every field of the structured parameter has its flag
                fields = set(resolve(properties[parameter], tools[tool])["properties"])
                assert {field for p, field in mapped.values() if p == parameter} == fields, (
                    command,
                    parameter,
                )


def test_required_parameters_are_positional_and_optional_ones_are_flags(commands, tools):
    for command, tool in PAIRS.items():
        required = set(tools[tool].get("required", []))
        for dest, (parameter, field) in mapping(
            command, commands[command], tools[tool]["properties"]
        ).items():
            positional = not commands[command][dest].option_strings
            assert positional == (field is None and parameter in required), (command, dest)


def test_defaults_agree(commands, tools):
    for command, tool in PAIRS.items():
        properties = tools[tool]["properties"]
        for dest, (parameter, field) in mapping(command, commands[command], properties).items():
            action = commands[command][dest]
            if not action.option_strings:
                continue  # required on both sides
            schema = properties[parameter]
            if field:
                schema = resolve(schema, tools[tool])["properties"][field]
            expected = schema.get("default")
            if (command, dest) in NEGATED:
                expected = not expected
            # A repeatable flag that was not given and an absent list are both 'nothing'; False, 0 and None
            # stay apart (type too, since False == 0).
            cli, mcp = (None if v == [] else v for v in (action.default, expected))
            assert (type(cli), cli) == (type(mcp), mcp), (command, dest)


def test_choices_agree(commands, tools):
    compared = 0
    for command, tool in PAIRS.items():
        properties = tools[tool]["properties"]
        for dest, (parameter, field) in mapping(command, commands[command], properties).items():
            schema = resolve(properties[parameter], tools[tool])
            if field:
                schema = resolve(schema["properties"][field], tools[tool])
            if schema.get("type") == "array":
                schema = resolve(schema["items"], tools[tool])
            allowed, choices = schema.get("enum"), commands[command][dest].choices
            assert (sorted(choices) if choices else None) == (sorted(allowed) if allowed else None), (
                command,
                dest,
            )
            compared += bool(allowed)
    assert compared >= 15  # roles, details, scopes, source types, designs, preference roles and modes


# One call per bound of the MCP schemas, just outside it. The CLI has no bounds of its own: the core
# refuses these, so both routes behave alike.
OUT_OF_RANGE = [
    ("search_studies", "limit", "minimum", lambda s, p: s.search_studies("opioid", limit=0)),
    ("search_studies", "limit", "maximum", lambda s, p: s.search_studies("opioid", limit=21)),
    ("search_studies", "query", "maxLength", lambda s, p: s.search_studies("x" * 2001)),
    ("compare_protocols", "question", "maxLength", lambda s, p: s.compare_protocols("x" * 2001, ["opioid"])),
    (
        "compare_protocols",
        "check_protocols",
        "minimum",
        lambda s, p: s.compare_protocols("q", ["opioid"], check_protocols=-1),
    ),
    (
        "compare_protocols",
        "check_protocols",
        "maximum",
        lambda s, p: s.compare_protocols("q", ["opioid"], check_protocols=21),
    ),
    ("get_protocol_outline", "offset", "minimum", lambda s, p: s.get_protocol_outline(p, offset=-1)),
    ("get_protocol_outline", "limit", "minimum", lambda s, p: s.get_protocol_outline(p, limit=0)),
    ("get_protocol_outline", "limit", "maximum", lambda s, p: s.get_protocol_outline(p, limit=201)),
    ("search_protocol_text", "limit", "minimum", lambda s, p: s.search_protocol_text(p, "design", limit=0)),
    ("search_protocol_text", "limit", "maximum", lambda s, p: s.search_protocol_text(p, "design", limit=31)),
    (
        "search_protocol_text",
        "max_chars",
        "minimum",
        lambda s, p: s.search_protocol_text(p, "design", max_chars=999),
    ),
    ("read_protocol_text", "offset", "minimum", lambda s, p: s.read_protocol_text(p, offset=-1)),
    ("read_protocol_text", "max_chars", "minimum", lambda s, p: s.read_protocol_text(p, max_chars=999)),
    ("read_protocol_text", "max_chars", "maximum", lambda s, p: s.read_protocol_text(p, max_chars=20001)),
    ("analyze_protocol", "offset", "minimum", lambda s, p: s.analyze_protocol("123", offset=-1)),
    ("analyze_protocol", "max_chars", "minimum", lambda s, p: s.analyze_protocol("123", max_chars=5999)),
    ("analyze_protocol", "max_chars", "maximum", lambda s, p: s.analyze_protocol("123", max_chars=150001)),
    (
        "analyze_protocol",
        "question",
        "maxLength",
        lambda s, p: s.analyze_protocol("123", question="x" * 2001),
    ),
    ("research_protocol", "question", "maxLength", lambda s, p: s.research_protocol(p, "x" * 2001)),
    (
        "cache_protocol_answer",
        "question",
        "maxLength",
        lambda s, p: s.cache_protocol_answer(
            p, "x" * 2001, ProtocolAnswer(answers=sample_analysis().key_notes)
        ),
    ),
    (
        "cache_protocol_analysis",
        "question",
        "maxLength",
        lambda s, p: s.cache_protocol_analysis(
            "123", "fingerprint", Extraction(), batch_offset=0, answer=ProtocolAnswer(), question="x" * 2001
        ),
    ),
]


# Every kind of bound JSON Schema puts on a number, a string or a list.
BOUNDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
}


async def test_the_core_refuses_what_the_schemas_bound(service, tools):
    protocol = (await service.get_protocol("123"))["protocol"]["protocol_id"]
    for tool, parameter, bound, call in OUT_OF_RANGE:
        with pytest.raises(RWEError) as error:
            result = call(service, protocol)
            if hasattr(result, "__await__"):
                await result
        assert error.value.code == "INVALID_INPUT", (tool, parameter, bound, error.value.code)
    # Every bound of every top-level parameter is covered above, each kind on its own
    bounded = {
        (tool, name, bound)
        for tool, schema in tools.items()
        for name, prop in schema["properties"].items()
        for bound in BOUNDS & set(resolve(prop, schema))
    }
    assert bounded == {(tool, parameter, bound) for tool, parameter, bound, _ in OUT_OF_RANGE}
