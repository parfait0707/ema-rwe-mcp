import argparse
import asyncio
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from .config import Settings
from .domain import AnalogousTerm, CodeCandidate, Extraction, RWEError, SourcePreference
from .ranking import ScreeningBlock
from .selection import SearchFilters
from .service import Service
from .storage import Repository, import_csv


def code_argument(value):
    try:
        system, code = value.rsplit(":", 1)
        return CodeCandidate(system=system.strip(), code=code.strip())
    except (ValueError, ValidationError) as exc:
        raise argparse.ArgumentTypeError("Expected SYSTEM:CODE, e.g. ICD-10:J84.9") from exc


def analogous_argument(value):
    try:
        term, relation = value.rsplit(":", 1)
        return AnalogousTerm(term=term.strip(), relation=relation.strip())
    except (ValueError, ValidationError) as exc:
        raise argparse.ArgumentTypeError(
            "Expected TERM:RELATION (broader/sibling/associated), e.g. renal impairment:broader"
        ) from exc


def parser():
    p = argparse.ArgumentParser(description="EMA non-interventional protocol search")
    commands = p.add_subparsers(dest="command", required=True)
    drugs = commands.add_parser("refresh-drugs")
    drugs.add_argument("--force", action="store_true")
    imp = commands.add_parser("import-csv")
    imp.add_argument("input", type=Path)
    imp.add_argument("--column-map", type=Path, help="JSON mapping: internal field to exact CSV header")
    inbox = commands.add_parser("import-download")
    inbox.add_argument("filename")
    inbox.add_argument("--column-map", type=Path)
    commands.add_parser("import-all", help="Import every CSV under studies/ then source_type/")
    backfill = commands.add_parser(
        "backfill-protocols",
        help="Fill medicines of studies whose export lists none: from their text, then their protocols",
    )
    backfill.add_argument("--interval", type=float, default=60.0, help="Seconds between studies (default 60)")
    backfill.add_argument("--limit", type=int, help="At most this many protocols in this run")
    backfill.add_argument("--no-download", action="store_true", help="Only the text step; no network")
    commands.add_parser("catalogue-status")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--all-studies", action="store_true")
    search.add_argument("--status", action="append")
    search.add_argument("--analyzed-only", action="store_true")
    search.add_argument("--synonym", action="append")
    search.add_argument("--code", action="append", type=code_argument)
    compare = commands.add_parser("compare")
    compare.add_argument("question")
    compare.add_argument("--query", action="append", default=[])
    compare.add_argument("--category-term", action="append", help="Umbrella term ranked below specific ones")
    compare.add_argument(
        "--blocks",
        type=Path,
        help="JSON file: list of {role, queries, category_terms}, AND-ed (replaces --query)",
    )
    compare.add_argument("--check-protocols", type=int, default=0, help="Check Study documents of the top N")
    compare.add_argument("--darwin-only", action="store_true")
    compare.add_argument("--synonym", action="append")
    compare.add_argument("--code", action="append", type=code_argument)
    compare.add_argument(
        "--prefer-source-type",
        action="append",
        default=[],
        choices=["claims", "registry", "ehr", "drug_dispensing_prescription", "other"],
    )
    compare.add_argument(
        "--source-role", default="any", choices=["any", "cohort", "outcome", "exposure", "covariate", "other"]
    )
    compare.add_argument("--source-mode", default="prefer", choices=["prefer", "only"])
    search.add_argument("--detail", choices=["compact", "full"], default="compact")
    compare.add_argument("--study-id", action="append", dest="study_ids", help="Explicit candidate choice")
    for cmd in (search, compare):
        cmd.add_argument(
            "--match-scope",
            choices=["concept", "analogous"],
            default="concept",
            help="analogous searches only clinically analogous concepts (the zero-hit fallback).",
        )
        cmd.add_argument("--analogous", action="append", type=analogous_argument, help="TERM:RELATION")
        cmd.add_argument(
            "--role",
            choices=["any", "outcome", "condition", "exposure"],
            default="any",
            help="Restrict matching to the catalogue columns for this role (plus title).",
        )
        cmd.add_argument("--country", action="append", default=[])
        cmd.add_argument(
            "--source-type",
            action="append",
            choices=["claims", "ehr", "registry", "others"],
            default=[],
            help="Catalogue narrowing by tagged source type; --prefer-source-type ranks PDF evidence.",
        )
        cmd.add_argument(
            "--study-design",
            action="append",
            choices=["case-control", "cohort", "cross-sectional", "ecological", "self-controlled"],
            default=[],
        )
    comparison = commands.add_parser("comparison")
    comparison.add_argument("comparison_id")
    comparison.add_argument(
        "--study-id", action="append", help="User-selected eligible IDs after completed screening."
    )
    plan = commands.add_parser("plan-search")
    plan.add_argument("question")
    plan.add_argument("--llm", action="store_true")
    local = commands.add_parser("local-protocols")
    local.add_argument("study_id")
    outline = commands.add_parser("outline")
    outline.add_argument("protocol_id")
    outline.add_argument("--offset", type=int, default=0)
    find = commands.add_parser("pdf-search")
    find.add_argument("protocol_id")
    find.add_argument("query")
    find.add_argument("--synonym", action="append")
    find.add_argument("--code", action="append", type=code_argument)
    read = commands.add_parser("pdf-read")
    read.add_argument("protocol_id")
    read.add_argument("--section-id")
    read.add_argument("--start-page", type=int, default=1)
    read.add_argument("--end-page", type=int)
    read.add_argument("--offset", type=int, default=0)
    ask = commands.add_parser("ask")
    ask.add_argument("protocol_id")
    ask.add_argument("question")
    ask.add_argument("--force", action="store_true")
    for name in ("study", "protocol", "analyze"):
        cmd = commands.add_parser(name)
        cmd.add_argument("study_id")
        cmd.add_argument("--refresh", action="store_true")
        if name == "protocol":
            cmd.add_argument("--version", default="latest")
            cmd.add_argument("--no-download", action="store_true")
        if name == "analyze":
            cmd.add_argument("--offset", type=int, default=0)
            cmd.add_argument("--max-chars", type=int, default=30000)
    submit = commands.add_parser("cache-analysis")
    submit.add_argument("study_id")
    submit.add_argument("fingerprint")
    submit.add_argument("input", type=Path)
    submit.add_argument("--coverage-complete", action="store_true")
    commands.add_parser("cleanup-cache")
    return p


async def run(args):
    settings = Settings()
    if args.command == "import-csv":
        mapping = json.loads(args.column_map.read_text(encoding="utf-8")) if args.column_map else None
        return import_csv(Repository(settings.db_path), args.input, mapping)
    service = Service(settings)
    try:
        filters = (
            SearchFilters(
                countries=args.country, data_source_types=args.source_type, study_designs=args.study_design
            )
            if args.command in {"search", "compare"}
            else None
        )
        match args.command:
            case "refresh-drugs":
                return await service.refresh_drug_dictionary(args.force)
            case "catalogue-status":
                return service.catalogue_status()
            case "import-download":
                mapping = json.loads(args.column_map.read_text(encoding="utf-8")) if args.column_map else None
                return service.import_catalogue_csv(args.filename, mapping)
            case "import-all":
                return service.import_all()
            case "backfill-protocols":
                return await service.backfill_protocols(args.interval, args.limit, not args.no_download)
            case "search":
                return service.search_studies(
                    args.query,
                    args.limit,
                    not args.all_studies,
                    args.status,
                    args.analyzed_only,
                    args.synonym,
                    args.code,
                    filters,
                    args.role,
                    args.detail,
                    args.match_scope,
                    args.analogous,
                )
            case "compare":
                if (
                    args.source_role != "any" or args.source_mode != "prefer"
                ) and not args.prefer_source_type:
                    raise RWEError(
                        "INVALID_INPUT", "--source-role/--source-mode requires --prefer-source-type."
                    )
                return await service.compare_protocols(
                    args.question,
                    args.query,
                    filters,
                    args.darwin_only,
                    args.synonym,
                    args.code,
                    SourcePreference(
                        types=args.prefer_source_type, role=args.source_role, mode=args.source_mode
                    )
                    if args.prefer_source_type
                    else None,
                    args.role,
                    args.study_ids,
                    args.match_scope,
                    args.analogous,
                    args.category_term,
                    [
                        ScreeningBlock.model_validate(b)
                        for b in json.loads(args.blocks.read_text(encoding="utf-8"))
                    ]
                    if args.blocks
                    else None,
                    args.check_protocols,
                )
            case "comparison":
                return await service.get_protocol_comparison(args.comparison_id, args.study_id)
            case "plan-search":
                return await service.plan_study_search(args.question, args.llm)
            case "local-protocols":
                return await service.list_local_protocols(args.study_id)
            case "outline":
                return await service.get_protocol_outline(args.protocol_id, args.offset)
            case "pdf-search":
                return await service.search_protocol_text(
                    args.protocol_id, args.query, synonyms=args.synonym, codes=args.code
                )
            case "pdf-read":
                return await service.read_protocol_text(
                    args.protocol_id, args.section_id, args.start_page, args.end_page, args.offset
                )
            case "ask":
                return await service.research_protocol(args.protocol_id, args.question, args.force)
            case "study":
                return (await service.get_study(args.study_id, args.refresh)).model_dump()
            case "protocol":
                return await service.get_protocol(
                    args.study_id, args.version, not args.no_download, args.refresh
                )
            case "analyze":
                return await service.analyze_protocol(
                    args.study_id, args.refresh, args.offset, args.max_chars
                )
            case "cache-analysis":
                analysis = Extraction.model_validate_json(args.input.read_text(encoding="utf-8"))
                return await service.cache_protocol_analysis(
                    args.study_id, args.fingerprint, analysis, args.coverage_complete
                )
            case "cleanup-cache":
                return {"removed": service.client.cleanup()}
    finally:
        await service.close()


def main():
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except RWEError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False))
        raise SystemExit(1)
    except (OSError, ValidationError, ValueError) as exc:
        print(json.dumps({"error": {"code": "INVALID_INPUT", "message": str(exc)}}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
