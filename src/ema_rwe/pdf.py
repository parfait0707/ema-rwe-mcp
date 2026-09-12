"""Native page-aware extraction. Raw PDF text remains in memory only."""

import re
from dataclasses import dataclass

import pymupdf

from .domain import Extraction, RWEError
from .terminology import search_units
from .vocabulary import canonical, contains, expand

RELEVANT = re.compile(
    r"design|method|setting|data source|database|population|inclusion|exclusion|exposure|"
    r"comparator|outcome|variable|definition|phenotyp|code list|codelist|concept|"
    r"analysis|analyses|statistic|follow.up|washout|objective|appendix|annex|cohort",
    re.IGNORECASE,
)
HEADING = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?\s+|[A-Z]\.\s+).{3,110}$")


def is_heading(line: str) -> bool:
    if line.rstrip().endswith("?"):
        return False
    if re.match(r"^(?:appendix|annex)\s+[A-Z0-9]+\b", line, re.IGNORECASE):
        return True
    if canonical(line) in {
        "study design",
        "research methods",
        "methods",
        "data sources",
        "study population",
        "statistical analysis",
        "references",
        "bibliography",
        "abstract",
        "appendices",
        "missing data",
        "data management",
        "variables",
        "study setting",
        "outcomes",
    }:
        return True
    if not HEADING.match(line) or re.search(r"\.{3,}|\s\d+\s*$", line):
        return False
    # Numbered database lists and footnotes are not section headings.
    if re.match(r"^\d+\.\d+", line):
        return True
    title = re.sub(r"^(?:\d+\.?|[A-Z]\.)\s+", "", line)
    return title.isupper() or bool(
        re.fullmatch(
            r"Study design|Data sources|Study population|Research methods|Data analysis|Disease definitions",
            title,
            re.IGNORECASE,
        )
    )


ROLES = {
    "references": ("references", "bibliography", "literature cited"),
    "administrative": ("responsible parties", "study team", "milestones", "governance", "signatures"),
    "background": ("background", "rationale", "introduction"),
    "abstract": ("abstract", "synopsis", "summary"),
    "appendix": ("appendix", "appendices", "annex", "supplement"),
    "methods": ("research methods", "methodology", "methods", "study design", "research design"),
    "data_sources": ("data sources", "data source", "study setting", "databases", "data provenance"),
    "population": ("population", "eligibility", "inclusion", "exclusion", "participants", "study subjects"),
    "definitions": (
        "variables",
        "phenotype",
        "phenotyping",
        "case definition",
        "diagnostic criteria",
        "outcomes",
        "exposure",
    ),
    "analysis": (
        "analysis",
        "analyses",
        "statistical",
        "missing data",
        "imputation",
        "sensitivity",
        "confounding",
    ),
}


def heading_role(heading: str | None) -> str:
    for role, terms in ROLES.items():
        if heading and any(contains(heading, term) for term in terms):
            return role
    return "unknown"


@dataclass
class Page:
    page: int
    text: str


def extract_pages(data: bytes) -> list[Page]:
    if not data.lstrip().startswith(b"%PDF-"):
        raise RWEError("PDF_PARSE_FAILED", "Response is not a PDF.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or len(doc) > 1500:
                raise RWEError("PDF_PARSE_FAILED", "Encrypted PDF or page limit exceeded.")
            pages = [Page(i + 1, page.get_text(sort=True)) for i, page in enumerate(doc)]
    except RWEError:
        raise
    except Exception as exc:
        raise RWEError("PDF_PARSE_FAILED", "Native PDF extraction failed.") from exc
    if sum(len(p.text.strip()) for p in pages) < 150:
        raise RWEError("PDF_OCR_REQUIRED", "No usable text layer; scanned PDF requires OCR (outside MVP).")
    if sum(len(p.text) for p in pages) > 5_000_000:
        raise RWEError("PDF_PARSE_FAILED", "Extracted text exceeds 5 million character limit.")
    return pages


def sections(pages: list[Page]) -> list[dict]:
    result = []

    def flush(buffer, page_number, current_heading):
        text = "\n".join(buffer).strip()
        if text:
            result.append(
                {
                    "page": page_number,
                    "section": current_heading,
                    "text": text,
                    "relevant": bool(RELEVANT.search((current_heading or "") + " " + text)),
                }
            )
        buffer.clear()

    checklist_heading = None
    for p in pages:
        appendix_headers = re.findall(r"(?im)^\s*((?:appendix|annex)\s+[^\n]{1,120})$", p.text)
        if appendix_headers:
            checklist_heading = (
                appendix_headers[0].strip()
                if len(appendix_headers) == 1 and "checklist" in appendix_headers[0].casefold()
                else None
            )
        if checklist_heading:
            # ENCePP's questionnaire contains numbered questions that mimic methods headings.
            # Keep the appendix boundary across pages; those questions are not study methods.
            result.append(
                {
                    "page": p.page,
                    "section": checklist_heading,
                    "text": p.text,
                    "relevant": False,
                    "checklist": True,
                }
            )
            continue
        if (
            re.search(r"(?im)^\s*(?:table of )?contents\s*$", p.text)
            or len(re.findall(r"\.{4,}\s*\d+", p.text)) >= 3
        ):
            result.append({"page": p.page, "section": None, "text": p.text, "relevant": False, "toc": True})
            continue
        lines = p.text.splitlines()
        current_heading = result[-1]["section"] if result and not result[-1].get("toc") else None
        buffer = []
        for line in lines:
            line = line.strip()
            if is_heading(line):
                flush(buffer, p.page, current_heading)
                current_heading = line
            if sum(map(len, buffer)) + len(line) > 5500:
                flush(buffer, p.page, current_heading)
            buffer.append(line)
        flush(buffer, p.page, current_heading)
    parent = None
    previous_number = ()
    for i, chunk in enumerate(result):
        heading = chunk["section"]
        match = re.match(r"^(\d+(?:\.\d+)*)\.?\s", heading or "")
        number = tuple(int(x) for x in match.group(1).split(".")) if match else ()
        role = heading_role(heading)
        warnings = []
        if number and previous_number and number < previous_number and heading != result[i - 1]["section"]:
            warnings.append("section_number_regression; verify heading against outline")
        if number:
            previous_number = number
        major_unnumbered = canonical(heading or "") in {
            "research methods",
            "methods",
            "methodology",
            "references",
            "bibliography",
            "abstract",
            "synopsis",
            "background",
            "rationale",
            "introduction",
        } or bool(re.match(r"^(appendix|annex)\b", heading or "", re.IGNORECASE))
        if heading and (len(number) == 1 or (not number and major_unnumbered) or parent is None):
            parent = {"heading": heading, "role": role}
        inherited = parent["role"] if parent else "unknown"
        if chunk.get("checklist"):
            role = "checklist"
            parent = {"heading": heading, "role": role}
        elif chunk.get("toc"):
            role = "contents"
            parent = None
        elif (
            inherited in {"references", "administrative", "background"}
            and len(number) != 1
            or role == "unknown"
        ):
            role = inherited
        chunk.update(
            section_id=f"s{i + 1:04}",
            role=role,
            context_role=parent["role"] if parent else "unknown",
            parent_section=parent["heading"] if parent else None,
            previous_section=result[i - 1]["section"] if i else None,
            next_section=result[i + 1]["section"] if i + 1 < len(result) else None,
            structure_warnings=warnings,
        )
        chunk["relevant"] = role not in {
            "contents",
            "references",
            "administrative",
            "background",
            "checklist",
        } and (role != "unknown" or chunk["relevant"] or bool(expand(chunk["text"][:1900])["concepts"]))
    return result


def search_sections(
    chunks: list[dict], query: str, limit: int = 10, synonyms: list[str] | None = None, codes=None
) -> dict:
    if not isinstance(query, str) or not query.strip() or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise RWEError("INVALID_INPUT", "Nonempty query and limit 1..30 required.")
    expansion = expand(query, synonyms, codes)
    terms = search_units(query, expansion)
    hits = []
    for chunk in chunks:
        matched = [t for t in terms if contains(chunk["text"], t)]
        if matched:
            score = len(matched) + 2 * sum(contains(chunk["section"] or "", t) for t in matched)
            if chunk["role"] in {"references", "contents", "background", "administrative", "checklist"}:
                score *= 0.15
            hits.append(dict(chunk, score=score, matched_terms=matched))
    hits.sort(key=lambda c: (-c["score"], c["page"], c["section_id"]))
    return {
        "query_expansion": expansion,
        "results": hits[:limit],
        "total_hits": len(hits),
        "note": "Searches all local PDF text, including sections excluded from initial extraction; verify role and neighbouring sections.",
    }


def normalize_quote(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip().casefold()


def validate_evidence(extraction: Extraction, pages: list[Page]):
    page_map = {p.page: normalize_quote(p.text) for p in pages}
    chunks = sections(pages)

    def validate_fact(fact, is_source=False, require_methods=False):
        for evidence in fact.evidence:
            quote = normalize_quote(evidence.quote)
            if evidence.page not in page_map or quote not in page_map[evidence.page]:
                raise RWEError("EVIDENCE_INVALID", f"Quote not found on PDF page {evidence.page}.")
            containing_chunks = [
                c for c in chunks if c["page"] == evidence.page and quote in normalize_quote(c["text"])
            ]
            if (
                require_methods
                and containing_chunks
                and all(
                    c["role"] in {"references", "contents", "background", "administrative", "checklist"}
                    for c in containing_chunks
                )
            ):
                raise RWEError(
                    "EVIDENCE_WRONG_SECTION",
                    "Method/data-source evidence comes only from background, references, checklist or administrative content.",
                )
            if evidence.section is not None:
                matching = [
                    c
                    for c in chunks
                    if c["page"] == evidence.page
                    and c["section"] == evidence.section
                    and quote in normalize_quote(c["text"])
                ]
                if not matching:
                    raise RWEError(
                        "EVIDENCE_INVALID",
                        "Section does not match the cited page/quote; "
                        "use the returned section label or null.",
                    )
        if is_source and not any(
            normalize_quote(fact.value) in normalize_quote(e.quote) for e in fact.evidence
        ):
            raise RWEError("EVIDENCE_INVALID", "Data source name must occur verbatim in its evidence quote.")

    for name in type(extraction).model_fields:
        value = getattr(extraction, name)
        if name in {"schema_version", "missing_information"} or value is None:
            continue
        for fact in value if isinstance(value, list) else [value]:
            validate_fact(fact, name == "data_sources", name != "key_notes")
