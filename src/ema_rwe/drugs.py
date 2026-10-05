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
            "salt_word_candidates": salt_word_candidates(records),
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
        "salt_word_candidates": salt_word_candidates(records),
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
        # Names only: an ATC code is never a cross-source key (the EMA record and the catalogue may label
        # the same code differently). Codes resolve to names through the catalogue (medicines.py).
        for term in [row["product_name"], " / ".join(row["ingredients"])]:
            terms.setdefault(canonical(term), []).append(i)
    pattern = re.compile(
        r"(?<![a-z0-9_])(?:"
        + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        + r")(?![a-z0-9_])"
    )
    # Base names of salted ingredients ('dabigatran etexilate' -> 'dabigatran'), for whole-term lookups
    # only: never scanned inside free text, where a short base word could match an unrelated phrase.
    for i, row in enumerate(records):
        for ingredient in row["ingredients"]:
            base = without_salt(ingredient)
            if len(row["ingredients"]) == 1 and base != canonical(ingredient):
                terms.setdefault(base, [])
                if i not in terms[base]:
                    terms[base].append(i)
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


# Trailing salt, ester and hydrate words observed in the catalogue and the EMA dictionary (2026-10-02,
# extended 2026-10-05 with choline, diolamine, meglumine, semisodium and anhydrous): words W such that both
# "X W" and "X" occur as medicine names there, kept when W is a counter-ion, ester or hydrate. They only add a
# base-name lookup key; the ingredient set of a product never changes. Words reviewed and rejected are in
# NOT_SALT_WORDS. A new candidate is added here by code review, never automatically.
SALT_WORDS = frozenset(
    [
        "acetate",
        "anhydrous",
        "benzoate",
        "besilate",
        "besylate",
        "bitartrate",
        "bromide",
        "calcium",
        "carbonate",
        "chloride",
        "choline",
        "citrate",
        "dihydrate",
        "dihydrochloride",
        "dimaleate",
        "diolamine",
        "dipropionate",
        "disodium",
        "etexilate",
        "fumarate",
        "furoate",
        "gluconate",
        "hemifumarate",
        "hemihydrate",
        "hydrobromide",
        "hydrochloride",
        "hydrogen",
        "magnesium",
        "maleate",
        "meglumine",
        "mesilate",
        "mesylate",
        "methanesulfonate",
        "monohydrate",
        "phosphate",
        "potassium",
        "propionate",
        "semisodium",
        "sodium",
        "subcitrate",
        "succinate",
        "sulfate",
        "sulphate",
        "tartrate",
        "tetrasodium",
        "tosilate",
        "tosylate",
        "trifenatate",
        "trihydrate",
        "xinafoate",
    ]
)


# Trailing words reviewed and rejected (they name another substance, a conjugate, a product or a form):
# a product line differs ('tenofovir disoproxil' vs 'tenofovir alafenamide'), so they are no lookup key.
NOT_SALT_WORDS = frozenset(
    [
        "alafenamide",
        "beta",
        "deruxtecan",
        "disoproxil",
        "emtansine",
        "flufenamide",
        "ivig",
        "live",
        "netarsudil",
        "pegol",
        "rdna",
        "rinfabate",
        "scig",
        "seretide",
    ]
)


def salt_word_candidates(records: list[dict]) -> list[str]:
    """Unreviewed trailing words W with both 'X W' and 'X' among the dictionary's ingredient names: reported by
    refresh_drug_dictionary for a person to classify into SALT_WORDS or NOT_SALT_WORDS."""
    names = {canonical(i) for row in records for i in row["ingredients"]}
    words = {n.rsplit(" ", 1)[1] for n in names if " " in n and n.rsplit(" ", 1)[0] in names}
    return sorted(words - SALT_WORDS - NOT_SALT_WORDS)


def without_salt(name: str) -> str:
    """The canonical name without trailing salt or ester words ('filgotinib maleate' -> 'filgotinib')."""
    words = canonical(name).split()
    while len(words) > 1 and words[-1] in SALT_WORDS:
        words.pop()
    return " ".join(words)


def ingredient_set(text: str) -> frozenset[str]:
    """The ingredients named by one medicine name, order-free; a single name gives a one-element set."""
    return frozenset(c for part in COMBINATION.split(text) if (c := canonical(part)))


def drug_expansion(query, whole_term: bool = False):
    """Products sharing the full ingredient set of any product name or INN in an English query.

    whole_term=True treats the query (or each item of a list) as one medicine name matched as a whole,
    never a name found inside a longer phrase.

    Japanese medicine names are translated before this step: by a sourced concept in the terminology
    dictionary (clinical_expansion feeds its English terms here) or by the caller/configured LLM.
    """
    records, meta, digest, (terms, pattern) = load_dictionary()
    items = query if isinstance(query, list) else [query]
    if whole_term:
        # Each item is one medicine name: it must equal a product name or an ingredient set as a whole,
        # apart from trailing salt or ester words ('tofacitinib citrate' finds tofacitinib products). This
        # is a retrieval hint only: a salt is never reported as the same formulation.
        indices = {i for item in items for i in terms.get(canonical(item), terms.get(without_salt(item), []))}
    else:
        seed = canonical(" ".join(items))
        indices = {i for match in pattern.finditer(seed) for i in terms[match.group()]} if pattern else set()
    for item in items:
        whole = ingredient_set(item)
        if len(whole) > 1:
            # A query that names a whole combination means that combination, not each of its ingredients.
            combination = {
                i for i, r in enumerate(records) if frozenset(map(canonical, r["ingredients"])) == whole
            }
            indices = indices | combination if whole_term else combination or indices
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
