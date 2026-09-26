"""Concept-to-English/code retrieval hints, distinct from protocol-derived definitions."""

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError

from .config import default_terminology_path
from .domain import CodeCandidate, Model, RWEError
from .vocabulary import canonical, contains


class Concept(Model):
    concept_id: str
    input_terms: list[str] = Field(min_length=1, max_length=40)
    english_terms: list[str] = Field(min_length=1, max_length=40)
    related_terms: list[str] = Field(default_factory=list, max_length=40)
    code_candidates: list[CodeCandidate] = Field(default_factory=list, max_length=40)


@lru_cache(maxsize=8)
def _load(path: str, mtime: int, size: int):
    if size > 2_000_000:
        raise RWEError("TERMINOLOGY_CONFIG_ERROR", "Terminology JSON exceeds 2 MB.")
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) > 3000:
            raise ValueError("Expected list of up to 3000 concepts")
        concepts = [Concept.model_validate(x) for x in data]
        for concept in concepts:
            if any(not t.isascii() or len(t) > 150 for t in concept.english_terms + concept.related_terms):
                raise ValueError("English search phrases must be ASCII and <=150 characters")
            for code in concept.code_candidates:
                code.origin = "local_dictionary"
                # A local declaration is not equivalent to this server checking a master.
                code.verification = "unverified"
        return concepts, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, ValidationError) as exc:
        raise RWEError("TERMINOLOGY_CONFIG_ERROR", "Invalid local concept/code dictionary.") from exc


def code_variants(candidate: CodeCandidate) -> list[str]:
    code = candidate.code.upper()
    variants = [code]
    system = canonical(candidate.system)
    # No numeric conversion: preserve leading zeros and keep vocabularies distinct.
    if system.startswith(("icd 10", "icd10")):
        compact = code.replace(".", "")
        if re.fullmatch(r"[A-Z][0-9][0-9A-Z][A-Z0-9]{0,4}", compact):
            variants += [compact, compact[:3] + "." + compact[3:] if len(compact) > 3 else compact]
    elif system.startswith(("icd 9", "icd9")):
        compact = code.replace(".", "")
        pos = 4 if compact.startswith("E") else 3
        if re.fullmatch(r"[EV]?\d{2,5}", compact):
            variants += [compact, compact[:pos] + "." + compact[pos:] if len(compact) > pos else compact]
    return list(dict.fromkeys(variants))


def inline_codes(query: str) -> list[CodeCandidate]:
    result = []
    for code in re.findall(
        r"\bATC\s*:?\s*([ABCDGHJLMNPRSV](?:\d{2}(?:[A-Z](?:[A-Z](?:\d{2})?)?)?)?)\b", query, re.IGNORECASE
    ):
        result.append(CodeCandidate(system="ATC", code=code.upper(), origin="caller"))
    # ATC and ICD-like strings are recognisable; bare numbers cannot identify SNOMED/RxNorm/OMOP.
    for code in re.findall(r"\b[ABCDGHJLMNPRSV]\d{2}[A-Z](?:[A-Z](?:\d{2})?)?\b", query, re.IGNORECASE):
        result.append(CodeCandidate(system="ATC", code=code, origin="caller"))
    for code in re.findall(r"\b[A-Za-z]\d{2}(?:\.[A-Za-z0-9]{1,4}|[A-Za-z0-9]{0,4})\b", query):
        if not any(c.code.casefold() == code.casefold() for c in result):
            result.append(
                CodeCandidate(system="ICD-10 (family inferred; edition unknown)", code=code, origin="caller")
            )
    return result


def clinical_expansion(query: str, codes: list[CodeCandidate] | None = None) -> dict:
    from .drugs import drug_expansion

    concepts, revision = [], "clinical-v2"
    path = os.getenv("EMA_TERMINOLOGY_PATH") or default_terminology_path()
    if path:
        try:
            stat = Path(path).stat()
            concepts, digest = _load(path, stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            raise RWEError("TERMINOLOGY_CONFIG_ERROR", "Cannot read EMA_TERMINOLOGY_PATH.") from exc
        revision += ":" + digest
    supplied = codes or []
    if len(supplied) > 40:
        raise RWEError("INVALID_INPUT", "At most 40 code candidates are allowed.")
    inputs = [*supplied, *inline_codes(query)]
    matched = [
        c
        for c in concepts
        if any(contains(query, t) for t in c.input_terms)
        or any(
            canonical(a.system) in {"icd 10", "icd10", "icd 10 family inferred edition unknown"}
            and canonical(b.system) in {"icd 10", "icd10"}
            and bool(set(code_variants(a)) & set(code_variants(b)))
            for a in inputs
            for b in c.code_candidates
        )
    ]
    # English names of matched concepts reach the medicines dictionary too, so a sourced dictionary
    # entry for a Japanese medicine name expands to every EMA product with the same ingredient set.
    drugs = drug_expansion(
        " ".join(
            [
                query,
                *(t for c in matched for t in c.english_terms),
                *(c.code for c in inputs if canonical(c.system) == "atc"),
            ]
        )
    )
    revision += ":" + drugs["dictionary_revision"]
    drug_codes = [
        CodeCandidate(
            system="ATC",
            code=code,
            label=r["inn_or_common_name"][:300],
            source_url=r["source_url"],
            origin="official_dictionary",
            verification="source_checked",
        )
        for r in drugs["matches"]
        for code in r["atc_codes"]
    ]
    candidates = [*inputs, *(v for c in matched for v in c.code_candidates), *drug_codes]
    candidates = list({(c.system, c.code, c.vocabulary_version): c for c in candidates}.values())
    return {
        "concepts": [c.concept_id for c in matched],
        "english_terms": list(
            dict.fromkeys([*(t for c in matched for t in c.english_terms), *drugs["english_terms"]])
        ),
        "drugs": drugs,
        "related_terms": list(dict.fromkeys(t for c in matched for t in c.related_terms)),
        "code_candidates": [dict(c.model_dump(), search_variants=code_variants(c)) for c in candidates],
        "terminology_revision": revision,
        "systems_to_check": [
            "ICD-10 (specify national modification)",
            "ICD-9-CM",
            "SNOMED CT",
            "Read/CTV3",
            "MedDRA",
            "OMOP concept_id (specify vocabulary)",
            "ATC",
            "RxNorm",
            "NDC",
            "LOINC",
            "local codes",
        ],
        "warning": "Code hints broaden retrieval; they are not equivalent disease definitions or evidence of study use. Confirm code system, version, source database, and outcome/exposure role in the protocol.",
    }


def search_units(query: str, expansion: dict) -> list[str]:
    """English phrases + whole code strings; never search the '9' from J84.9 alone."""
    words, code_terms = _content_words(query, expansion)
    return list(
        dict.fromkeys(
            [
                *words,
                *expansion["synonyms"],
                *expansion["clinical"]["english_terms"],
                *expansion["clinical"]["related_terms"],
                *code_terms,
            ]
        )
    )[:350]


def _content_words(query: str, expansion: dict) -> tuple[list[str], list[str]]:
    """Query words minus codes, code-system names and stop words, plus the whole code strings."""
    remainder = query
    code_terms = [v for c in expansion["clinical"]["code_candidates"] for v in c["search_variants"]]
    for term in sorted(code_terms, key=len, reverse=True):
        remainder = re.sub(r"(?<!\w)" + re.escape(term) + r"(?!\w)", " ", remainder, flags=re.IGNORECASE)
    remainder = re.sub(
        r"\b(?:ICD[- ]?(?:10|9)(?:[- ]CM)?|ATC|SNOMED(?: CT)?|RxNorm|OMOP|LOINC|MedDRA)\s*:?",
        " ",
        remainder,
        flags=re.IGNORECASE,
    )
    stop = {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "and",
        "or",
        "with",
        "find",
        "studies",
        "study",
        "how",
        "what",
        "is",
        "used",
    }
    # Do not partially extract ASCII from a non-English word.
    words = [t for t in re.findall(r"\w+", remainder) if t.isascii() and t.casefold() not in stop]
    return words, code_terms


def search_phrases(query: str, expansion: dict) -> list[list[str]]:
    """Word groups for the catalogue index: words in a group must co-occur (NEAR); groups are OR-ed.

    The query itself is one group when it has at most four content words. Longer free text falls
    back to single words so that a sentence-style question still retrieves candidates.
    """
    words, code_terms = _content_words(query, expansion)
    groups = [words] if 0 < len(words) <= 4 else [[w] for w in words]
    for term in [
        *expansion["synonyms"],
        *expansion["clinical"]["english_terms"],
        *expansion["clinical"]["related_terms"],
        *code_terms,
    ]:
        groups.append(re.findall(r"\w+", term))
    return [g for g in groups if g][:350]


def proposed_codes(raw: list[dict]) -> list[CodeCandidate]:
    try:
        if not isinstance(raw, list) or len(raw) > 40:
            raise ValueError("At most 40 proposed codes")
        codes = [CodeCandidate.model_validate(c) for c in raw]
    except (ValidationError, ValueError) as exc:
        raise RWEError(
            "SCHEMA_VALIDATION_FAILED", "Invalid code candidates; include code system and code."
        ) from exc
    for code in codes:
        code.origin, code.verification = "llm", "unverified"
    return codes
