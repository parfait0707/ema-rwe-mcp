"""Official EMA product-name / INN-common-name / ATC lookup, cached offline."""

import hashlib
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path

import httpx

from .config import Settings
from .domain import RWEError, atomic_write
from .vocabulary import canonical

SOURCE_URL = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
MAX_BYTES = 30_000_000
TTL = 7 * 86400


def dictionary_path():
    return Path(os.getenv("EMA_DRUG_DICTIONARY_PATH") or Settings().db_path.parent / "ema-medicines.json")


def parse_medicines(raw):
    try:
        payload = json.loads(raw)
        rows = payload["data"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
            raise ValueError("Invalid data array")
        records = []
        for row in rows:
            if row["category"].casefold() != "human":
                continue
            name = row["name_of_medicine"].strip()
            # Preserve the source's INN/common-name field, never substitute active substance.
            inn = row["international_non_proprietary_name_common_name"].strip()
            url = row["medicine_url"]
            if not name or not inn:
                continue
            if not url.startswith("https://www.ema.europa.eu/") or len(name) > 500 or len(inn) > 2000:
                raise ValueError("Unexpected medicine fields")
            records.append(
                {
                    "product_name": name,
                    "inn_or_common_name": inn,
                    "ingredients": [v.strip() for v in inn.split(";") if v.strip()],
                    "atc_codes": re.findall(r"\b[A-Z]\d{2}[A-Z]{2}\d{2}\b", row["atc_code_human"]),
                    "source_url": url,
                    "source_updated_at": row.get("last_updated_date"),
                }
            )
        if not records:
            raise ValueError("No usable human medicines")
        return records, payload.get("meta", {})
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RWEError("DRUG_DICTIONARY_INVALID", "EMA medicines JSON schema could not be verified.") from exc


async def refresh_dictionary(force=False, transport=None):
    path = dictionary_path()
    if path.exists() and not force and time.time() - path.stat().st_mtime < TTL:
        records, meta, digest, _ = load_dictionary()
        return {
            "cached": True,
            "records": len(records),
            "source": SOURCE_URL,
            "source_metadata": meta,
            "sha256": digest,
            "path": str(path),
            "network_requests": 0,
        }
    try:
        async with (
            httpx.AsyncClient(timeout=60, follow_redirects=False, transport=transport) as client,
            client.stream("GET", SOURCE_URL) as response,
        ):
            response.raise_for_status()
            raw = bytearray()
            async for block in response.aiter_bytes():
                raw.extend(block)
                if len(raw) > MAX_BYTES:
                    raise RWEError("DRUG_DICTIONARY_INVALID", "EMA medicines JSON exceeds 30 MB.")
        records, meta = parse_medicines(raw)
    except httpx.HTTPError as exc:
        raise RWEError(
            "DRUG_DICTIONARY_DOWNLOAD_FAILED", "Could not download the official EMA medicines JSON."
        ) from exc
    atomic_write(path, bytes(raw))
    return {
        "cached": False,
        "records": len(records),
        "source": SOURCE_URL,
        "source_metadata": meta,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "path": str(path),
        "network_requests": 1,
    }


def _index(records):
    if not records:
        return {}, None
    terms = {}
    for i, row in enumerate(records):
        for term in [row["product_name"], " / ".join(row["ingredients"]), *row["atc_codes"]]:
            terms.setdefault(canonical(term), []).append(i)
    pattern = re.compile(
        r"(?<![a-z0-9_])(?:"
        + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        + r")(?![a-z0-9_])"
    )
    return terms, pattern


@lru_cache(maxsize=4)
def _load(path, mtime, size):
    if size > MAX_BYTES:
        raise RWEError("DRUG_DICTIONARY_INVALID", "EMA medicines JSON exceeds 30 MB.")
    raw = Path(path).read_bytes()
    records, meta = parse_medicines(raw)
    return records, meta, hashlib.sha256(raw).hexdigest(), _index(records)


def load_dictionary():
    path = dictionary_path()
    if path.exists():
        stat = path.stat()
        return _load(str(path), stat.st_mtime_ns, stat.st_size)
    # No dictionary on disk: expand nothing rather than pretend coverage; needs_refresh tells the caller.
    return [], {"coverage": "none; call refresh_drug_dictionary"}, "missing", _index([])


# Separators of a combination written as one name ('empagliflozin and metformin', 'A / B', 'A + B').
COMBINATION = re.compile(r"\s*(?:/|\+|,|;|\band\b|\bwith\b)\s*", re.IGNORECASE)


def ingredient_set(text: str) -> frozenset[str]:
    """The ingredients named by one medicine name, order-free; a single name gives a one-element set."""
    return frozenset(c for part in COMBINATION.split(text) if (c := canonical(part)))


def drug_expansion(query):
    """Products sharing the full ingredient set of any name/INN/ATC in an English (or code) query.

    Japanese medicine names are translated before this step: by a sourced concept in the terminology
    dictionary (clinical_expansion feeds its English terms here) or by the caller/configured LLM.
    """
    records, meta, digest, (terms, pattern) = load_dictionary()
    seed = canonical(query)
    indices = {i for match in pattern.finditer(seed) for i in terms[match.group()]} if pattern else set()
    whole = ingredient_set(query)
    if len(whole) > 1:
        # A query that names a whole combination means that combination, not each of its ingredients.
        combination = {
            i for i, r in enumerate(records) if frozenset(map(canonical, r["ingredients"])) == whole
        }
        indices = combination or indices
    # Only expand to products with the SAME FULL ingredient tuple; no class-member or single/combination equivalence.
    groups = {tuple(sorted(canonical(v) for v in records[i]["ingredients"])) for i in indices}
    matched = (
        [r for r in records if tuple(sorted(canonical(v) for v in r["ingredients"])) in groups]
        if groups
        else []
    )
    names = list(
        dict.fromkeys(
            t for r in matched for t in [" / ".join(r["ingredients"]), r["product_name"]] if t.isascii()
        )
    )
    path = dictionary_path()
    return {
        "matches": matched[:100],
        "total_matches": len(matched),
        "truncated": len(matched) > 100 or len(names) > 100,
        "english_terms": names[:100],
        "dictionary_revision": digest,
        "source_metadata": meta,
        "needs_refresh": not path.exists() or time.time() - path.stat().st_mtime > TTL,
        "warning": "EMA INN/common name field retained as labelled. Product names and ATC assignments are retrieval hints, not evidence of identical formulation or study exposure. Coverage is EMA centralised human medicines; national brands may be absent.",
    }
