"""Log of query terms no user dictionary recognised (written only while a dictionary is configured)."""

import json
from pathlib import Path

from .domain import (
    now,
)


def read_log(path: Path) -> dict:
    """The unmatched-query log; a missing or unreadable file counts as empty."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def record_unmatched(path: Path, text: str, tool: str, expansion: dict) -> bool:
    """Log a non-English query that neither the concept dictionary nor the drug dictionary recognised.

    The log lives beside the database (not inside it, which is committed) and is the evidence for
    deciding which concepts to add to a user dictionary (data/dictionaries/). Nothing is logged while
    no dictionary is configured. English-only queries are not logged:
    the index is English, so they need no translation.
    """
    text = " ".join(text.split())[:200]
    clinical = expansion["clinical"]
    if (
        not clinical["dictionaries"]  # without a dictionary every Japanese query is unmatched by design
        or not text
        or text.isascii()
        or expansion["concepts"]
        or clinical["concepts"]
        or clinical["drugs"]["total_matches"]
    ):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    log = read_log(path)
    entry = log.setdefault(text, {"count": 0, "first_seen": now(), "tools": []})
    entry["count"] += 1
    entry["last_seen"] = now()
    if tool not in entry["tools"]:
        entry["tools"].append(tool)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True


def unmatched_summary(path: Path, limit: int = 20) -> dict:
    """Most frequent unrecognised queries, for deciding whether the dictionary needs new concepts."""
    log = read_log(path)
    ranked = sorted(log.items(), key=lambda kv: (-kv[1]["count"], kv[1].get("last_seen", "")))
    return {
        "path": str(path),
        "distinct": len(log),
        "top": [{"text": text, **entry} for text, entry in ranked[:limit]],
        "note": "Japanese queries that matched no concept or medicine of the configured dictionaries "
        "(recorded only while a dictionary is configured). Add frequent ones to a dictionary under "
        "data/dictionaries/.",
    }
