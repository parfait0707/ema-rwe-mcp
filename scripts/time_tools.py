"""Time the tool calls of one caller extraction per study, without an LLM.

    uv run scripts/time_tools.py <study_id> [<study_id> ...]

For each study it reads every batch of analyze_protocol, caches an empty batch at each offset (as a caller
does between batches), then calls the outline, search, read and research tools once. It works on a temporary
copy of the database (EMA_DB_PATH or the default), so no batch or analysis is left behind; protocols and HTTP
responses are read from, and added to, the usual folders. A protocol that is not archived yet is fetched from
EMA under the normal rate limit. See docs/validation.md (2026-11-19) for recorded results.
"""

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ema_rwe.config import Settings
from ema_rwe.domain import Extraction
from ema_rwe.pdf import parse_stats
from ema_rwe.service import Service


async def run(study_ids: list[str]) -> dict[str, list[float]]:
    settings = Settings()
    settings.protocol_dir = settings.protocol_dir or settings.db_path.parent / "protocols"
    rows: dict[str, list[float]] = {}
    with tempfile.TemporaryDirectory() as folder:
        settings.db_path = Path(shutil.copy(settings.db_path, folder))
        service = Service(settings)

        async def timed(label, call):
            started = time.perf_counter()
            result = await call
            rows.setdefault(label, []).append((time.perf_counter() - started) * 1000)
            return result

        try:
            for study in study_ids:
                await service.get_protocol(study)  # network, if any, stays out of the timings
                batch = await timed("analyze_protocol (first batch)", service.analyze_protocol(study))
                protocol, fingerprint = batch["source"]["protocol_id"], batch["source"]["fingerprint"]
                reading, offset, characters, batches = batch["source"]["reading"], 0, 0, 0
                while True:
                    characters += sum(len(c["text"]) for c in batch["sections"])
                    batches += 1
                    await timed(
                        "cache_protocol_analysis (batch)",
                        service.cache_protocol_analysis(
                            study, fingerprint, Extraction(), batch_offset=offset, reading=reading
                        ),
                    )
                    if batch["next_offset"] is None:
                        break
                    offset = batch["next_offset"]
                    batch = await timed(
                        "analyze_protocol (next batch)", service.analyze_protocol(study, offset=offset)
                    )
                await timed("get_protocol_outline", service.get_protocol_outline(protocol))
                await timed(
                    "search_protocol_text", service.search_protocol_text(protocol, "inclusion criteria")
                )
                await timed(
                    "read_protocol_text", service.read_protocol_text(protocol, start_page=1, end_page=2)
                )
                await timed(
                    "research_protocol", service.research_protocol(protocol, "What is the study design?")
                )
                rows.setdefault("batches", []).append(batches)
                rows.setdefault("section characters", []).append(characters)
        finally:
            await service.close()
    return rows


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rows = asyncio.run(run(sys.argv[1:]))
    print(f"{'call':34}{'n':>4}{'mean':>10}{'total':>10}   (ms; batches and characters are counts)")
    for label, values in rows.items():
        print(f"{label:34}{len(values):>4}{sum(values) / len(values):>10.0f}{sum(values):>10.0f}")
    stats = parse_stats()
    print(f"PDF parses: {stats['parsed']} done, {stats['reused']} reused, {stats['parse_ms']:.0f} ms parsing")


if __name__ == "__main__":
    main()
