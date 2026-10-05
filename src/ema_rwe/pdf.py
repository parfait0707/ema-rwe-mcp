"""Native page-aware extraction. Raw PDF text remains in memory only."""

import re
from dataclasses import dataclass

import pymupdf

from .domain import Extraction, RWEError, with_notes
from .terminology import search_units
from .vocabulary import canonical, contains, expand

RELEVANT = re.compile(
    r"design|method|setting|data source|database|population|inclusion|exclusion|exposure|"
    r"comparator|outcome|variable|definition|phenotyp|code list|codelist|concept|"
    r"analysis|analyses|statistic|follow.up|washout|objective|appendix|annex|cohort|consent|enrol",
    re.IGNORECASE,
)
# Section numbers: arabic (9, 9.2) or roman (II, II.4); a leading zero ('0.03 MG/ML') is a value, not a section.
SECTION_NUMBER = re.compile(r"^((?:[1-9]\d?|[IVX]{1,4})(?:\.\d{1,2})*)\.?\s+(\S.{2,109})$")
LETTERED = re.compile(r"^[A-Z]\.\s+(\S.{2,109})$")
ROMAN = {"I": 1, "V": 5, "X": 10}
# Chapter titles of the EMA PASS protocol template (EMA/623947/2012), matched as whole titles: a single-level
# numbered line with one of them is a heading even in title case ('13 References'), unlike a numbered list item.
CHAPTER_TITLES = {
    "table of contents",
    "list of abbreviations",
    "responsible parties",
    "abstract",
    "amendments and updates",
    "milestones",
    "rationale and background",
    "research question and objectives",
    "research methods",
    "study design",
    "setting",
    "variables",
    "data sources",
    "study size",
    "data management",
    "data analysis",
    "quality control",
    "limitations of the research methods",
    "other aspects",
    "protection of human subjects",
    "management and reporting of adverse events",
    "management and reporting of adverse events adverse reactions",
    "plans for disseminating and communicating study results",
    "references",
    "annexes",
    "study population",
    "disease definitions",
}


def roman_value(text: str) -> int | None:
    values = [ROMAN.get(ch) for ch in text]
    if None in values:
        return None
    total = sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
    return total if total > 0 else None


def section_number(heading: str | None) -> tuple[int, ...]:
    """(2, 4) for 'II.4 Abstract' or '2.4 Abstract'; () when the heading carries no section number."""
    match = SECTION_NUMBER.match(heading or "")
    if not match:
        return ()
    first, *rest = match.group(1).split(".")
    value = int(first) if first.isdigit() else roman_value(first)
    return (value, *map(int, rest)) if value else ()


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
    if re.search(r"\.{3,}|\s\d+\s*$", line):
        return False
    if (match := SECTION_NUMBER.match(line)) and section_number(line):
        if len(section_number(line)) > 1:
            return True  # '9.2 Setting', 'II.4 Abstract': multi-level numbering is not a list item
        title = match.group(2)
    elif match := LETTERED.match(line):
        title = match.group(1)
    else:
        return False
    # A single-level number is also how lists are written: only an all-caps or template chapter title counts.
    return title.isupper() or canonical(title) in CHAPTER_TITLES


ROLES = {
    "contents": (
        "table of contents",
        "list of tables",
        "list of figures",
        "list of appendices",
        "list of annexes",
    ),
    "references": ("references", "bibliography", "literature cited"),
    "administrative": ("responsible parties", "study team", "milestones", "governance", "signatures"),
    "background": ("background", "rationale", "introduction"),
    "abstract": ("abstract", "synopsis", "summary"),
    "appendix": ("appendix", "appendices", "annex", "annexes", "supplement"),
    # Protocol changes: read, but superseded conditions must not be extracted as current ones.
    "amendments": (
        "amendments",
        "amendment",
        "document history",
        "description of changes",
        "protocol changes",
    ),
    # Study conduct chapters of the PASS template (ethics, safety reporting, dissemination). They may hold
    # study-specific conditions or definitions (consent before enrolment, adverse event definitions).
    "conduct": (
        "protection of human subjects",
        "ethics",
        "ethical",
        "consent",
        "reporting of adverse events",
        "safety reporting",
        "disseminating",
        "dissemination",
        "publication",
    ),
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
    "objectives": ("research question", "research questions", "objectives", "objective"),
}


# Three separate decisions use the section role:
# - NON_METHOD_ROLES are not read for extraction (and rank low in search);
# - LOW_PRIORITY_ROLES (and unknown) are read only when the section itself shows method content;
# - EVIDENCE_REJECT_ROLES are the roles whose text is confidently not this study's methods, so a method
#   fact quoted only from them is rejected. An uncertain role never rejects a verbatim quote.
NON_METHOD_ROLES = {"references", "contents", "background", "administrative", "checklist"}
LOW_PRIORITY_ROLES = {"unknown", "conduct", "amendments"}
EVIDENCE_REJECT_ROLES = {"references", "contents", "background", "checklist"}


def heading_role(heading: str | None) -> str:
    for role, terms in ROLES.items():
        if heading and any(contains(heading, term) for term in terms):
            return role
    return "unknown"


@dataclass
class Page:
    page: int
    text: str
    # From the PDF bookmark outline when the file has one: the deepest bookmark covering this page
    # and its top-level ancestor. None when the PDF has no bookmarks (text headings are used instead).
    chapter: str | None = None
    chapter_top: str | None = None
    # Every bookmark title from the top level down to `chapter` ('12 Annexes / 12.3 Annex 3 / 12.3.2 ...').
    chapter_path: str | None = None


def apply_bookmarks(pages: list[Page], toc: list) -> None:
    """Label each page with the bookmark chapter that starts on or before it (pymupdf get_toc rows)."""
    entries = sorted(
        ((int(p), int(lvl), str(t).strip()) for lvl, t, p in toc if p and p > 0), key=lambda e: e[0]
    )
    if not entries:
        return
    ancestors: dict[int, str] = {}
    i = 0
    current = None
    for page in pages:
        while i < len(entries) and entries[i][0] <= page.page:
            _, level, title = entries[i]
            ancestors = {lvl: t for lvl, t in ancestors.items() if lvl < level}
            ancestors[level] = title
            current = title
            i += 1
        if current is not None:
            page.chapter = current
            page.chapter_top = ancestors.get(min(ancestors)) if ancestors else current
            page.chapter_path = " / ".join(ancestors[lvl] for lvl in sorted(ancestors)) or current


# Version of the section/role/reading rules; part of the analysis fingerprint, so a change re-extracts.
PARSER_VERSION = "structural-v6"


def extract_pages(data: bytes, use_outline: bool = True) -> list[Page]:
    """Page texts with the bookmark outline applied; use_outline=False ignores the PDF's own outline
    (structure evaluation measures text-only detection against the bookmarks)."""
    if not data.lstrip().startswith(b"%PDF-"):
        raise RWEError("PDF_PARSE_FAILED", "Response is not a PDF.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or len(doc) > 1500:
                raise RWEError("PDF_PARSE_FAILED", "Encrypted PDF or page limit exceeded.")
            pages = [Page(i + 1, page.get_text(sort=True)) for i, page in enumerate(doc)]
            apply_bookmarks(pages, doc.get_toc() if use_outline else [])
    except RWEError:
        raise
    except Exception as exc:
        raise RWEError("PDF_PARSE_FAILED", "Native PDF extraction failed.") from exc
    if sum(len(p.text.strip()) for p in pages) < 150:
        raise RWEError("PDF_OCR_REQUIRED", "No usable text layer; scanned PDF requires OCR (outside MVP).")
    if sum(len(p.text) for p in pages) > 5_000_000:
        raise RWEError("PDF_PARSE_FAILED", "Extracted text exceeds 5 million character limit.")
    return pages


def text_layer(data: bytes) -> str | None:
    """full, partial (half or more pages without text) or none (image-only, needs OCR); None if unparsable."""
    try:
        pages = extract_pages(data)
    except RWEError as exc:
        return "none" if exc.code == "PDF_OCR_REQUIRED" else None
    blank = sum(len(p.text.strip()) < 50 for p in pages)
    return "partial" if blank * 2 >= len(pages) else "full"


def sections(pages: list[Page]) -> list[dict]:
    result = []

    chapters = {p.page: (p.chapter, p.chapter_top, p.chapter_path) for p in pages}

    def flush(buffer, page_number, current_heading):
        text = "\n".join(buffer).strip()
        if text:
            chapter, chapter_top, chapter_path = chapters.get(page_number, (None, None, None))
            result.append(
                {
                    "page": page_number,
                    "section": current_heading,
                    "chapter": chapter,
                    "chapter_top": chapter_top,
                    "chapter_path": chapter_path,
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
        if (
            current_heading
            and result[-1].get("chapter") != p.chapter
            and not any(is_heading(line.strip()) for line in lines)
        ):
            # A page without a text heading that starts a new bookmark chapter begins that chapter, so the
            # previous page's heading does not carry over. A page with headings may start the chapter
            # midway; its leading text still continues the previous section.
            current_heading = None
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
        number = section_number(heading)
        role = heading_role(heading)
        basis = "heading" if role != "unknown" else "none"
        if role == "unknown" and chunk.get("chapter"):
            # Bookmark outline beats a missed text heading; the top-level chapter supplies the context.
            role = heading_role(chunk["chapter"])
            if role == "unknown":
                role = heading_role(chunk.get("chapter_top"))
            basis = "bookmark" if role != "unknown" else basis
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
        top = chunk.get("chapter_top")
        if (
            top
            and top != (result[i - 1].get("chapter_top") if i else None)
            and heading_role(top) != "unknown"
        ):
            # A top-level bookmark chapter with a known role becomes the parent context; an outline title
            # without one ('Part B') keeps the parent found from text headings.
            parent = {"heading": top, "role": heading_role(top)}
        if heading and (len(number) == 1 or (not number and major_unnumbered) or parent is None):
            parent = {"heading": heading, "role": role}
        inherited = parent["role"] if parent else "unknown"
        if chunk.get("checklist"):
            role, basis = "checklist", "page_type"
            parent = {"heading": heading, "role": role}
        elif chunk.get("toc"):
            role, basis = "contents", "page_type"
            parent = None
        elif (
            inherited in {"references", "administrative", "background"}
            and len(number) != 1
            or role == "unknown"
        ):
            role, basis = inherited, ("parent" if inherited != "unknown" else "none")
        chunk.update(
            section_id=f"s{i + 1:04}",
            role=role,
            context_role=parent["role"] if parent else "unknown",
            parent_section=parent["heading"] if parent else None,
            previous_section=result[i - 1]["section"] if i else None,
            next_section=result[i + 1]["section"] if i + 1 < len(result) else None,
            structure_warnings=warnings,
            role_basis=basis,  # heading, bookmark, parent, page_type or none: how sure the role is
        )
        chunk["relevant"] = role not in NON_METHOD_ROLES and (
            role not in LOW_PRIORITY_ROLES
            or chunk["relevant"]
            or bool(expand(chunk["text"][:1900])["concepts"])
        )
    return result


APPENDIX_REF = re.compile(r"\b(?:annex|appendix)\s+([IVX]+|[A-Za-z]?\d{1,3}[A-Za-z]?|[A-Z])\b", re.IGNORECASE)
CODE_LIST_TITLE = re.compile(
    r"code|list|atc|icd|snomed|read code|definition|algorithm|variable", re.IGNORECASE
)


def reading_order(chunks: list[dict]) -> list[dict]:
    """Relevant sections to read for extraction.

    With a bookmark outline: body chapters first, then only the appendices the body refers to
    ("Annex 3", "Appendix B"), so a 240-page protocol is read at the chapters that define the study
    plus its code lists. Without bookmarks every relevant section is returned as before.
    """
    relevant = [c for c in chunks if c["relevant"]]
    if not any(c.get("chapter") for c in chunks):
        return relevant
    body = [c for c in relevant if c["role"] != "appendix"]
    referenced = {m.group(1).casefold() for c in body for m in APPENDIX_REF.finditer(c["text"]) if m.group(1)}

    def is_referenced(chunk) -> bool:
        # The whole outline path: a sub-bookmark ('12.3.2 Terms for outcome mapping') belongs to its annex.
        label = " ".join(
            filter(None, (chunk.get("chapter_path") or chunk.get("chapter"), chunk.get("section")))
        )
        chapter = chunk.get("chapter") or ""
        if "checklist" in chapter.casefold():
            return False  # ENCePP checklists mimic method headings but are not this study's methods
        # Code lists and variable definitions are read even without an explicit cross-reference.
        if CODE_LIST_TITLE.search(chapter):
            return True
        return any(m.group(1).casefold() in referenced for m in APPENDIX_REF.finditer(label) if m.group(1))

    appendices = [c for c in relevant if c["role"] == "appendix" and is_referenced(c)]
    return sorted(body + appendices, key=lambda c: (c["page"], c["section_id"]))


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
            # A bookmark chapter title naming the concept outranks a body mention.
            score += 3 * sum(contains(chunk.get("chapter") or "", t) for t in matched)
            if chunk["role"] in NON_METHOD_ROLES:
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


def validate_evidence(extraction: Extraction, pages: list[Page], chunks: list[dict] | None = None):
    page_map = {p.page: normalize_quote(p.text) for p in pages}
    chunks = sections(pages) if chunks is None else chunks

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
                and all(c["role"] in EVIDENCE_REJECT_ROLES for c in containing_chunks)
            ):
                raise RWEError(
                    "EVIDENCE_WRONG_SECTION",
                    "Method/data-source evidence comes only from background, references, contents or checklist content.",
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
        if name in {"schema_version", "missing_information", "figure_pages"} or value is None:
            continue
        if not isinstance(value, list) and not hasattr(value, "evidence"):
            validate_evidence(value, pages, chunks)  # nested block such as cohort / design_schema
            continue
        for fact in value if isinstance(value, list) else [value]:
            validate_fact(fact, name in {"data_sources", "source_assessments"}, name != "key_notes")


REDACTION = re.compile(r"\b(?:CCI|redacted|commercially confidential)\b", re.IGNORECASE)
PROPER_TERM = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9\-]{3,}|[A-Z]{2,}[A-Za-z0-9\-]*|\d+(?:\.\d+)?\s?(?:mg|days?|months?|years?|%))\b"
)


def audit_extraction(
    extraction: Extraction, pages: list[Page], catalogue_sources: list[str] = ()
) -> list[str]:
    """Deterministic self-checks appended to missing_information (never rejecting the extraction).

    - redacted passages (CCI markers) near outcome/endpoint text;
    - catalogue-listed data sources absent from the extracted data_sources;
    - proper names / quantities in fact values that no evidence quote contains.
    """
    notes: list[str] = []
    text_by_page = {p.page: p.text for p in pages}
    redacted = sorted(
        p
        for p, t in text_by_page.items()
        if REDACTION.search(t) and re.search(r"outcome|endpoint", t, re.IGNORECASE)
    )
    if redacted:
        notes.append(
            f"Redacted (CCI) content on pages {redacted} near outcome/endpoint text; definitions there are unavailable."
        )
    extracted = " ".join(d.value for d in extraction.data_sources).casefold()
    missing = [s for s in catalogue_sources if s and canonical(s) not in canonical(extracted)]
    if missing:
        notes.append(f"Catalogue lists data source(s) not found in the extraction: {missing[:8]}.")
    unquoted: list[str] = []
    for name in ("study_design", "exposure", "comparator", "population"):
        fact = getattr(extraction, name)
        if fact is None:
            continue
        quotes = normalize_quote(" ".join(e.quote for e in fact.evidence))
        terms = {t for t in PROPER_TERM.findall(fact.value) if normalize_quote(t) not in quotes}
        if terms:
            unquoted.append(f"{name}: {sorted(terms)[:6]}")
    if unquoted:
        notes.append("Value terms without a verbatim quote (verify against the PDF): " + "; ".join(unquoted))
    return notes


def finalize_extraction(
    extraction: Extraction, pages: list[Page], catalogue_sources: list[str] = ()
) -> tuple[Extraction, int]:
    """Prune unverifiable evidence, then append the dropped count and audit notes to missing_information."""
    pruned, dropped = prune_unverifiable(extraction, pages)
    notes = []
    if dropped:
        notes.append(
            f"{dropped} provider evidence item(s) failed verbatim/page verification and were dropped."
        )
    notes += audit_extraction(pruned, pages, catalogue_sources)
    if notes:
        pruned.missing_information = with_notes(pruned.missing_information, notes)
    return pruned, dropped


_LIST_FIELDS = {
    name for name, prop in Extraction.model_json_schema()["properties"].items() if prop.get("type") == "array"
}


def prune_unverifiable(extraction: Extraction, pages: list[Page]) -> tuple[Extraction, int]:
    """Server-side extractions: keep evidence that passes validate_evidence, drop facts left without any.

    Returns the pruned extraction and the number of dropped evidence items/facts, so one bad quote
    from the provider does not reject the whole study the way a caller-submitted extraction would.
    """
    chunks = sections(pages)
    page_map = {p.page: normalize_quote(p.text) for p in pages}
    data = extraction.model_dump()
    dropped = 0

    def verifies(name, fact, evidence_items) -> bool:
        single = dict(fact, evidence=evidence_items)
        # Nested block fields (cohort criteria, time windows) are checked under the method rules of
        # a top-level list field; validation only depends on the field name for source/key-note rules.
        field = name if name in Extraction.model_fields else "outcomes"
        payload = {field: [single]} if field in _LIST_FIELDS else {field: single}
        try:
            validate_evidence(Extraction.model_validate(payload), pages, chunks)
            return True
        except RWEError:
            return False

    def repaired(name, fact, evidence) -> list[dict]:
        """Verbatim quote on a neighbouring page -> fix the page; data source name missing from the
        quote but present on the page -> add a short verbatim window containing the name."""
        quote = normalize_quote(evidence["quote"])
        for page in (evidence["page"] - 1, evidence["page"] + 1):
            if quote in page_map.get(page, "") and verifies(name, fact, [dict(evidence, page=page)]):
                return [dict(evidence, page=page)]
        if name in {"data_sources", "source_assessments"} and quote in page_map.get(evidence["page"], ""):
            text = normalize_quote(next(p.text for p in pages if p.page == evidence["page"]))
            at = text.find(normalize_quote(fact["value"]))
            if at >= 0:
                window = {
                    "page": evidence["page"],
                    "section": None,
                    "quote": text[max(0, at - 40) : at + 120],
                }
                # Keep the original quote only if the pair passes every rule (it may have failed
                # the section check as well as the name check); otherwise the window alone.
                for candidate in ([evidence, window], [window]):
                    if verifies(name, fact, candidate):
                        return candidate
        return []

    def with_section(evidence: dict) -> dict:
        """Replace the section label with the one derived from where the quote sits on the page.

        Provider labels are not trusted: a heading carried over from an earlier page passes the
        containment check while naming the wrong section. No unique chunk -> null.
        """
        quote = normalize_quote(evidence["quote"])
        page_text = page_map.get(evidence["page"], "")
        labels = {
            c["section"]
            for c in chunks
            if c["page"] == evidence["page"]
            and c["section"]
            and quote in normalize_quote(c["text"])
            # A heading carried over from an earlier page is not this page's section.
            and normalize_quote(c["section"]) in page_text
        }
        return dict(evidence, section=labels.pop() if len(labels) == 1 else None)

    def prune_block(block: dict) -> None:
        nonlocal dropped
        for name, value in list(block.items()):
            if name in {"schema_version", "missing_information", "figure_pages"} or value is None:
                continue
            if isinstance(value, dict) and "evidence" not in value:  # nested block (cohort, design_schema)
                prune_block(value)
                continue
            is_list = isinstance(value, list)
            kept = []
            for fact in value if is_list else [value]:
                good = []
                for evidence in map(with_section, fact["evidence"]):
                    if verifies(name, fact, [evidence]):
                        good.append(evidence)
                    elif fixed := repaired(name, fact, evidence):
                        good.extend(fixed)
                    else:
                        dropped += 1
                if good:
                    kept.append(dict(fact, evidence=good[:8]))
            block[name] = kept if is_list else (kept[0] if kept else None)

    prune_block(data)
    return type(extraction).model_validate(data), dropped
