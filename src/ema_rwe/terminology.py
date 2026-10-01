"""Concept-to-English/code retrieval hints, distinct from protocol-derived definitions."""

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError

from .config import default_dictionary_dir
from .domain import AnalogousTerm, CodeCandidate, Model, RWEError
from .vocabulary import canonical, contains


class Concept(Model):
    concept_id: str
    input_terms: list[str] = Field(min_length=1, max_length=40)
    english_terms: list[str] = Field(min_length=1, max_length=40)
    related_terms: list[str] = Field(default_factory=list, max_length=40)
    code_candidates: list[CodeCandidate] = Field(default_factory=list, max_length=40)
    # Different clinical concepts, kept out of the default search (see AnalogousTerm).
    analogous_terms: list[AnalogousTerm] = Field(default_factory=list, max_length=40)


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


def dictionary_paths() -> list[Path]:
    """Configured concept dictionaries: EMA_TERMINOLOGY_PATH (a file or a directory of *.json),
    else <data>/dictionaries/*.json. Empty means the MCP client translates every question."""
    configured = os.getenv("EMA_TERMINOLOGY_PATH")
    root = Path(configured) if configured else default_dictionary_dir()
    if configured and not root.exists():
        raise RWEError("TERMINOLOGY_CONFIG_ERROR", "Cannot read EMA_TERMINOLOGY_PATH.")
    if root.is_dir():  # hidden files (e.g. macOS ._x.json) are not dictionaries
        return [p for p in sorted(root.glob("*.json")) if not p.name.startswith(".")]
    return [root] if root.is_file() else []


def load_dictionaries() -> tuple[list[tuple[str, Concept]], str, list[str]]:
    """(file name, concept) pairs from every configured dictionary, a revision over their contents, and
    the file names (an empty file is still a configured dictionary)."""
    paths = dictionary_paths()
    concepts, digests = [], []
    for path in paths:
        try:
            stat = path.stat()
            loaded, digest = _load(str(path), stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            raise RWEError("TERMINOLOGY_CONFIG_ERROR", f"Cannot read dictionary {path.name}.") from exc
        except RWEError as exc:
            raise RWEError(exc.code, f"{exc.message} File: {path.name}.") from exc
        concepts += [(path.name, c) for c in loaded]
        digests.append(digest)
    return concepts, ":".join(digests), [p.name for p in paths]


def clinical_expansion(
    query: str, codes: list[CodeCandidate] | None = None, whole_terms: list[str] | None = None
) -> dict:
    from .drugs import drug_expansion

    sourced, digest, dictionary_names = load_dictionaries()
    revision = "clinical-v3" + (":" + digest if digest else "")
    concepts = [c for _, c in sourced]
    source_of = {id(c): name for name, c in sourced}
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
    english = [t for c in matched for t in c.english_terms]
    drugs = (
        drug_expansion([*whole_terms, *english], whole_term=True)
        if whole_terms is not None
        else drug_expansion(" ".join([query, *english]))
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
    # Where each expansion term came from, so a search result can say why a study matched.
    term_sources = {}
    for c in matched:
        for term in [*c.english_terms, *c.related_terms, *(a.term for a in c.analogous_terms)]:
            term_sources.setdefault(term, "dictionary:" + source_of[id(c)])
        for code in c.code_candidates:
            for variant in code_variants(code):
                term_sources.setdefault(variant, "dictionary:" + source_of[id(c)])
    for term in drugs["english_terms"]:
        term_sources.setdefault(term, "ema_medicines")
    origin_labels = {"official_dictionary": "ema_medicines", "local_dictionary": "dictionary"}
    for c in candidates:
        for variant in code_variants(c):
            term_sources.setdefault(variant, origin_labels.get(c.origin, c.origin))
    return {
        "dictionaries": dictionary_names,
        "concepts": [c.concept_id for c in matched],
        "matched_input_terms": [t for c in matched for t in c.input_terms if contains(query, t)],
        "english_terms": list(
            dict.fromkeys([*(t for c in matched for t in c.english_terms), *drugs["english_terms"]])
        ),
        "drugs": drugs,
        "related_terms": list(dict.fromkeys(t for c in matched for t in c.related_terms)),
        "analogous_terms": list(
            {
                a.term.casefold(): {**a.model_dump(), "concept_id": c.concept_id}
                for c in matched
                for a in c.analogous_terms
            }.values()
        ),
        "code_candidates": [dict(c.model_dump(), search_variants=code_variants(c)) for c in candidates],
        "term_sources": term_sources,
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
    words, code_terms, _ = _content_words(query, expansion)
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


def _content_words(query: str, expansion: dict) -> tuple[list[str], list[str], int]:
    """Query words minus codes, code-system names and stop words, plus the whole code strings, and the
    number of tokens between the first and last content word in the query text (stop words included:
    the index keeps them, so NEAR must allow for them)."""
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
    tokens = re.findall(r"\w+", remainder)
    kept = [i for i, t in enumerate(tokens) if t.isascii() and t.casefold() not in stop]
    between = kept[-1] - kept[0] - 1 if len(kept) > 1 else 0
    return [tokens[i] for i in kept], code_terms, between


def labelled_phrases(query: str, expansion: dict, split_long: bool = True) -> list[tuple[str, list]]:
    """(matched term, word group) pairs for the catalogue index: words in a group must co-occur
    (NEAR); groups are OR-ed. The label is what a result reports as the term it matched.

    The query itself is one group when it has at most four content words. Longer free text falls
    back to single words so that a sentence-style question still retrieves candidates, unless
    split_long is false: a caller's explicit search term (compare_protocols query variants, e.g. an
    ICD-10 title such as "drug-induced interstitial lung disease") stays one phrase, because its
    single words ("drug", "disease") would match most of the catalogue.
    """
    words, code_terms, between = _content_words(query, expansion)
    as_phrase = 0 < len(words) <= 4 or (words and not split_long)
    # The group holds content words only, so its NEAR distance comes from the query's own token span
    # plus one spare token: "risk of stroke in patients with atrial fibrillation" still matches itself.
    phrase = (words, between + 1) if len(words) > 1 else words
    groups = [(" ".join(words), phrase)] if as_phrase else [(w, [w]) for w in words]
    for term in [
        *expansion["synonyms"],
        *expansion["clinical"]["english_terms"],
        *expansion["clinical"]["related_terms"],
        *code_terms,
    ]:
        groups.append((term, re.findall(r"\w+", term)))
    unique = {}
    for label, group in groups:
        if group and (not isinstance(group, tuple) or group[0]):
            unique.setdefault(label.casefold(), (label, group))
    return list(unique.values())[:350]


def search_phrases(query: str, expansion: dict, split_long: bool = True) -> list[list[str]]:
    return [group for _, group in labelled_phrases(query, expansion, split_long)]


def term_source(term: str, expansion: dict, extra: list[AnalogousTerm] | None = None) -> str:
    """caller, llm, vocabulary, dictionary:<file>, ema_medicines, or query (the query string itself).
    A query string that is also an expansion term reports that term's source."""
    if term in expansion["term_sources"]:
        return expansion["term_sources"][term]
    return "caller" if any(a.term.casefold() == term.casefold() for a in extra or []) else "query"


def analogous_phrases(
    expansion: dict, extra: list[AnalogousTerm] | None = None
) -> list[tuple[str, list[str]]]:
    """(term, word group) pairs for the analogous-concept search: dictionary entries plus caller terms."""
    terms = [a["term"] for a in expansion["clinical"]["analogous_terms"]] + [a.term for a in extra or []]
    unique = {t.casefold(): t for t in reversed(terms)}  # first spelling wins, as in analogous_terms()
    return [(t, g) for t in reversed(unique.values()) if (g := re.findall(r"\w+", t))][:80]


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
