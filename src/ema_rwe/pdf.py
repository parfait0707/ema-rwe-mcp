"""Native page-aware extraction. Raw PDF text remains in memory only."""

import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from .domain import Extraction, RWEError, with_notes
from .terminology import search_units
from .vocabulary import canonical, contains, expand

RELEVANT = re.compile(
    r"design|method|setting|data source|database|population|inclusion|exclusion|exposure|"
    r"comparator|outcome|variable|definition|phenotyp|code list|codelist|concept|"
    r"analysis|analyses|statistic|follow.up|washout|objective|appendix|annex|cohort|eligib",
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
    "appendices",
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
    # Protocols have tens of chapters at most: '79.15 Closed reduction ...' is a procedure code, not a section.
    return (value, *map(int, rest)) if value and value <= 30 else ()


def is_heading(line: str) -> bool:
    if line.rstrip().endswith("?"):
        return False
    if re.match(r"^(?:appendix|annex)\s+[A-Z0-9]+\b", line, re.IGNORECASE):
        # 'Appendix I. The following protocols will continue to be developed' is a sentence citing the
        # appendix: running text carries lower-case function words, titles hardly any.
        return sum(w in ENGLISH_WORDS for w in re.findall(r"\b[a-z]+\b", line)) < 3
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
            # '9.2 Setting', 'II.4 Abstract': multi-level numbering is not a list item; a lower-case word after
            # the number ('3.3 million, by applying ...') is a value in running text.
            return not match.group(2)[0].islower()
        title = match.group(2)
        if match.group(1).isalpha() and canonical(title) not in CHAPTER_TITLES:
            # 'IV Q5106', 'IV ORAL': a route or code in a table row, not a roman chapter number.
            return title.isupper() and len(re.findall(r"[A-Za-z]{3,}", title)) >= 2
    elif match := LETTERED.match(line):
        title = match.group(1)
    else:
        # An unnumbered all-caps line naming a method topic ('DATA SOURCE, STUDY DESIGN AND METHODOLOGY').
        words = re.findall(r"[A-Za-z]{2,}", line)
        return line.isupper() and 2 <= len(words) <= 12 and heading_role(line) in METHOD_ROLES
    # A single-level number is also how lists are written: only an all-caps or template chapter title counts.
    return title.isupper() or canonical(title) in CHAPTER_TITLES


ROLES = {
    # A list of annexes is the annexes themselves (structure); lists of tables and figures are navigation.
    "contents": ("table of contents", "list of tables", "list of figures"),
    "references": ("references", "bibliography", "literature cited"),
    "administrative": ("responsible parties", "study team", "milestones", "governance", "signatures"),
    # 'rationale' alone also titles method subsections ('Context and rationale for definition of time 0').
    "background": (
        "background",
        "introduction",
        "rationale and background",
        "study rationale",
        "scientific rationale",
    ),
    "abstract": ("abstract", "synopsis", "summary"),
    # Protocol changes: read, but superseded conditions must not be extracted as current ones.
    "amendments": (
        "amendments",
        "amendment",
        "document history",
        "description of changes",
        "protocol changes",
    ),
    "appendix": ("appendix", "appendices", "annex", "annexes", "supplement"),
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
    "objectives": (
        "research question",
        "research questions",
        "objectives",
        "objective",
        "aims",
        "aim",
        "goals",
        "goal",
    ),
}


# Separate decisions use the section role:
# - NON_METHOD_ROLES are not read for extraction (and rank low in search); an unknown role is read only
#   when the section itself shows method content. Conduct and amendment chapters are read: they may state
#   study-specific conditions (consent before enrolment, adverse event definitions and collection windows,
#   a removed washout), and their role tells the extractor how to read them.
# - EVIDENCE_REJECT_ROLES are the roles whose text is confidently not this study's methods, so a method
#   fact quoted only from them is rejected. An uncertain role never rejects a verbatim quote.
NON_METHOD_ROLES = {"references", "contents", "background", "administrative", "checklist"}
# A section whose own heading names one of these keeps it inside a background chapter.
METHOD_ROLES = {"methods", "objectives", "population", "definitions", "data_sources", "analysis"}
EVIDENCE_REJECT_ROLES = {"references", "contents", "background", "checklist"}


def heading_role(heading: str | None) -> str:
    if heading and re.sub(r"^[\dIVX.]+\s+", "", canonical(heading)).startswith("rationale"):
        return "background"  # 'Rationale', '5 Rationale for the study'
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
    # Canonical titles of the outline entries that start on this page: such a line is a section heading.
    outline_titles: set[str] = field(default_factory=set)
    # Each such entry's own (chapter, top-level chapter, path), for the text below its title line.
    outline_entries: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # Where the outline came from: 'bookmarks', 'toc' (a verified text table of contents) or None.
    outline_source: str | None = None
    # Canonical line text -> (largest font size, bold) from the PDF's text spans, and the document's body
    # font size: layout support for chapter headings when there is no outline.
    line_styles: dict[str, tuple[float, bool]] = field(default_factory=dict)
    body_size: float = 0.0


def apply_line_styles(pages: list[Page], doc) -> None:
    sizes: Counter = Counter()
    for page, pdf_page in zip(pages, doc):
        for block in pdf_page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = canonical("".join(s["text"] for s in spans))
                bold = all(s["flags"] & 16 or "bold" in s["font"].casefold() for s in spans)
                page.line_styles[text] = (max(s["size"] for s in spans), bold)
                for s in spans:
                    sizes[round(s["size"], 1)] += len(s["text"])
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    for page in pages:
        page.body_size = body


def apply_bookmarks(pages: list[Page], toc: list, source: str = "bookmarks") -> None:
    """Label each page with the bookmark chapter that starts on or before it (pymupdf get_toc rows).

    A list of tables, figures or contents and the entries under it are navigation, not structure: they point
    at single tables inside other chapters (often at the first one, not at the list), so they are skipped.
    Contents pages are recognised from their text instead.
    """
    rows, navigation_level = [], None
    for lvl, title, page in toc:
        if navigation_level is not None and lvl > navigation_level:
            continue
        navigation_level = None
        if heading_role(str(title)) == "contents":
            navigation_level = lvl
            continue
        rows.append((lvl, title, page))
    toc = rows
    entries = sorted(
        ((int(p), int(lvl), " ".join(str(t).split())) for lvl, t, p in toc if p and p > 0), key=lambda e: e[0]
    )
    if not entries:
        return
    by_page = {p.page: p for p in pages}
    for start, _, title in entries:
        if start in by_page:
            by_page[start].outline_titles.add(canonical(title))
    ancestors: dict[int, str] = {}
    i = 0
    current = None
    for page in pages:
        while i < len(entries) and entries[i][0] <= page.page:
            start, level, title = entries[i]
            ancestors = {lvl: t for lvl, t in ancestors.items() if lvl < level}
            ancestors[level] = title
            current = title
            if start == page.page:
                page.outline_entries[canonical(title)] = (
                    title,
                    ancestors[min(ancestors)],
                    " / ".join(ancestors[lvl] for lvl in sorted(ancestors)),
                )
            i += 1
        if current is not None:
            page.chapter = current
            page.chapter_top = ancestors.get(min(ancestors)) if ancestors else current
            page.chapter_path = " / ".join(ancestors[lvl] for lvl in sorted(ancestors)) or current
            page.outline_source = source


CAPTION = re.compile(r"(?:table|figure|fig\.)\s*[A-Z]?\d", re.IGNORECASE)
TOC_LINE = re.compile(
    r"^\s*(?P<number>(?:[1-9]\d?|[IVX]{1,4})(?:\.\d{1,2})*\.?)?\s*(?P<title>\S.*?)\s*(?:\.{3,}|…+)\s*(?P<page>\d{1,3})\s*$"
)


def text_outline(pages: list[Page]) -> list[list]:
    """Outline rows [level, title, physical page] read from a text table of contents, or [] when unverified.

    Printed page numbers differ from physical ones by the front matter. An entry counts only where its
    heading is printed as a line of the body; the printed-to-physical offset must agree for at least three
    entries and two fifths of all entries. Entries anchored at that offset (±1 page) are kept, and so are
    unanchored appendix or template chapter entries placed by the offset; table and figure captions are
    navigation and are skipped.
    """
    toc_pages = [p for p in pages[:25] if sum(bool(TOC_LINE.match(l)) for l in p.text.splitlines()) >= 3]
    if not toc_pages:
        return []
    last_toc = max(p.page for p in toc_pages)
    entries = []
    for page in toc_pages:
        for line in page.text.splitlines():
            if m := TOC_LINE.match(line):
                number = m["number"] or ""  # as printed ('9.2.'), for the outline title
                key = canonical(f"{number} {m['title']}")
                entries.append((number, m["title"].strip(), int(m["page"]), key))
    body = {
        p.page: {canonical(l) for l in p.text.splitlines() if l.strip()} for p in pages if p.page > last_toc
    }
    anchors = [(entry, [pg for pg, lines in body.items() if entry[3] in lines]) for entry in entries]
    offsets = Counter(off for entry, found in anchors for off in {pg - entry[2] for pg in found})
    if not offsets:
        return []
    offset, support = offsets.most_common(1)[0]
    if support < 3 or support < 0.4 * len(entries):
        return []
    rows, numbered_level, last_page = [], 0, 0
    for (number, title, printed, _), found in anchors:
        if CAPTION.match(title):
            continue  # a list-of-tables or list-of-figures line: navigation, not structure
        chapter_like = not number and (
            re.match(r"(?:appendix|annex|appendices|annexes)\b", title, re.IGNORECASE)
            or canonical(title) in CHAPTER_TITLES
        )
        near = [pg for pg in found if abs(pg - (printed + offset)) <= 1]
        if near:
            page = min(near, key=lambda pg: abs(pg - printed - offset))
        elif chapter_like and last_page <= printed + offset <= len(pages):
            # An annex of rotated tables prints no heading line; the verified offset still places it.
            page = printed + offset
        else:
            continue
        if number:
            level = numbered_level = len(section_number(f"{number.rstrip('.')} title"))
        elif chapter_like:
            level = 1
        else:  # an unnumbered line under a numbered entry is its sub-item; unnumbered entries are siblings
            level = numbered_level + 1
        rows.append([max(level, 1), f"{number} {title}".strip(), page])
        last_page = max(last_page, page)
    return rows


# Version of the section/role/reading rules; part of the analysis fingerprint, so a change re-extracts.
PARSER_VERSION = "structural-v23"


def mark_contents_titles(pages: list[Page], rows: list[list]) -> None:
    """A verified contents page tells where sections start, not which chapter later text belongs to: its
    entries can drift from the body (a contents page numbering methods 8.x for a body's 9.x), so chapters
    and roles keep coming from the text headings."""
    if not rows:
        return
    by_page = {p.page: p for p in pages}
    for _, title, page in rows:
        if page in by_page:
            by_page[page].outline_titles.add(canonical(title))
    for page in pages:
        page.outline_source = "toc"


def extract_pages(data: bytes, use_outline: bool = True) -> list[Page]:
    """Page texts with an outline applied: the PDF's bookmarks, else a verified text table of contents.
    use_outline=False ignores the bookmarks (structure evaluation measures text-only detection against them)."""
    if not data.lstrip().startswith(b"%PDF-"):
        raise RWEError("PDF_PARSE_FAILED", "Response is not a PDF.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or len(doc) > 1500:
                raise RWEError("PDF_PARSE_FAILED", "Encrypted PDF or page limit exceeded.")
            pages = [Page(i + 1, page.get_text(sort=True)) for i, page in enumerate(doc)]
            apply_bookmarks(pages, doc.get_toc() if use_outline else [])
            if not any(p.chapter for p in pages):
                mark_contents_titles(pages, text_outline(pages))
            if not any(p.chapter for p in pages):
                apply_line_styles(pages, doc)
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


ENGLISH_WORDS = frozenset(
    [
        "the",
        "of",
        "and",
        "to",
        "in",
        "for",
        "with",
        "will",
        "be",
        "is",
        "are",
        "this",
        "that",
        "by",
        "on",
        "from",
        "or",
        "as",
    ]
)


def english(pages: list[Page]) -> bool:
    """Whether the protocol body is in English: these function words are about a fifth of English prose."""
    words = re.findall(r"[a-z]+", " ".join(p.text for p in pages).casefold())
    return not words or sum(w in ENGLISH_WORDS for w in words) >= 0.08 * len(words)


CITATION = re.compile(r"\bet al\b|\bdoi\b|\b(?:19|20)\d{2}\b", re.IGNORECASE)


def citation_like(text: str) -> bool:
    """Reference lists carry years, 'et al' and DOIs densely; code tables and protocol text do not."""
    return len(CITATION.findall(text)) * 1000 >= 2 * len(text)


def starts_section(line: str, page: Page, last_chapter: int | None = None) -> bool:
    """A detected heading, a line naming an outline entry (bookmark or verified contents) of its page, or,
    without any outline, a layout chapter: the next chapter number in a larger or bold font."""
    if line and canonical(line) in page.outline_titles:
        return True
    if is_heading(line):
        number = section_number(line)
        # A numbered line far beyond the current chapter is a table row or code ('25.3 mg', '17.2 ...').
        return not (len(number) > 1 and last_chapter is not None and number[0] > last_chapter + 3)
    return page.outline_source is None and layout_chapter(line, page, last_chapter)


def layout_chapter(line: str, page: Page, last_chapter: int | None) -> bool:
    """'5 Study Procedures' after chapter 4, set larger or bold than the body text. A numbered list item
    breaks the chapter sequence ('1. Patients ...' inside chapter 7) or is set in the body font."""
    number = section_number(line)
    match = SECTION_NUMBER.match(line)
    if len(number) != 1 or not match or last_chapter is None or number[0] != last_chapter + 1:
        return False
    title = match.group(2)
    style = page.line_styles.get(canonical(line))
    return (
        len(title) <= 80
        and not title.rstrip().endswith((".", ",", ";", ":"))
        and style is not None
        and (style[0] >= page.body_size + 1 or style[1])
    )


CHECKLIST_TITLE = re.compile(r"checklist\s+for\s+study\s+protocols", re.IGNORECASE)


def names_checklist(line: str, heading: bool) -> bool:
    """A heading naming a checklist, or the ENCePP questionnaire's own title line (headings may misspell it)."""
    return len(line) <= 140 and (
        bool(CHECKLIST_TITLE.search(line)) or (heading and "checklist" in line.casefold())
    )


def questionnaire_follows(page_lines: list[list[str]], index: int, line_number: int) -> bool:
    """Questions and answer columns (Yes / No / N/A) within the next two pages: the title is not merely
    listed in a contents page or a list of annexes."""
    text = "\n".join(
        page_lines[index][line_number + 1 :] + [l for p in page_lines[index + 1 : index + 3] for l in p]
    )
    return text.count("?") >= 5 and bool(re.search(r"\bYes\b[\s\S]{0,40}\bNo\b|\bN/A\b", text))


def ends_checklist(line: str, page: Page) -> bool:
    """The next appendix, outline entry or top-level chapter after the questionnaire."""
    if "checklist" in line.casefold() or line.rstrip().endswith("?"):
        return False
    return (
        bool(re.match(r"^(?:appendix|annex)\s+[A-Z0-9]+\b", line, re.IGNORECASE))
        or canonical(line) in page.outline_titles
        or (is_heading(line) and len(section_number(line)) == 1)
    )


def sections(pages: list[Page]) -> list[dict]:
    result = []

    chapters = {p.page: (p.chapter, p.chapter_top, p.chapter_path) for p in pages}
    outline = {p.page: p.outline_source for p in pages}
    # The chapter of the text being read: a page's outline chapter starts at its title line, so the text
    # above that line still belongs to the previous page's chapter.
    current = {"chapter": (None, None, None)}

    def flush(buffer, page_number, current_heading, checklist=False):
        text = "\n".join(buffer).strip()
        if text:
            chapter, chapter_top, chapter_path = current["chapter"]
            result.append(
                {
                    "page": page_number,
                    "section": current_heading,
                    "chapter": chapter,
                    "chapter_top": chapter_top,
                    "chapter_path": chapter_path,
                    "outline_source": outline[page_number] if chapter else None,
                    "text": text,
                    "relevant": not checklist and bool(RELEVANT.search((current_heading or "") + " " + text)),
                    **({"checklist": True} if checklist else {}),
                }
            )
        buffer.clear()

    in_checklist = None  # the heading of the ENCePP questionnaire being read, if any
    unconfirmed = set()  # (page, heading) named as a checklist without a questionnaire after it
    last_chapter = None  # first number of the latest numbered heading, for the layout chapter sequence
    page_lines = [[line.strip() for line in p.text.splitlines()] for p in pages]
    for index, p in enumerate(pages):
        lines = page_lines[index]
        if not in_checklist and (
            re.search(r"(?im)^\s*(?:table of )?contents\s*$", p.text)
            or len(re.findall(r"\.{4,}\s*\d+", p.text)) >= 3
        ):
            chapter, chapter_top, chapter_path = chapters[p.page]
            result.append(
                {
                    "page": p.page,
                    "section": None,
                    "chapter": chapter,
                    "chapter_top": chapter_top,
                    "chapter_path": chapter_path,
                    "outline_source": p.outline_source,
                    "text": p.text,
                    "relevant": False,
                    "toc": True,
                }
            )
            continue
        current_heading = result[-1]["section"] if result and not result[-1].get("toc") else None
        if (
            current_heading
            and result[-1].get("chapter") != p.chapter
            and not any(starts_section(line, p) for line in lines)
        ):
            # A page without a text heading that starts a new bookmark chapter begins that chapter, so the
            # previous page's heading does not carry over. A page with headings may start the chapter
            # midway; its leading text still continues the previous section.
            current_heading = None
        buffer = []
        titled = any(canonical(line) in p.outline_titles for line in lines if line)
        current["chapter"] = chapters[pages[index - 1].page] if titled and index else chapters[p.page]
        for n, line in enumerate(lines):
            if titled and line and canonical(line) in p.outline_titles:
                flush(buffer, p.page, in_checklist or current_heading, checklist=bool(in_checklist))
                current["chapter"] = p.outline_entries.get(canonical(line), chapters[p.page])
            if in_checklist:
                if not ends_checklist(line, p):
                    buffer.append(line)
                    continue
                flush(buffer, p.page, in_checklist, checklist=True)
                in_checklist = None
            heading = starts_section(line, p, last_chapter)
            if names_checklist(line, heading):
                if questionnaire_follows(page_lines, index, n):
                    # ENCePP's questionnaire has numbered questions that mimic methods headings: keep it as one
                    # checklist block, from this line to the next appendix or top-level chapter.
                    flush(buffer, p.page, current_heading)
                    in_checklist = line
                    buffer.append(line)
                    continue
                unconfirmed.add((p.page, line))
            if heading:
                flush(buffer, p.page, current_heading)
                current_heading = line
                last_chapter = section_number(line)[0] if section_number(line) else last_chapter
            if sum(map(len, buffer)) + len(line) > 5500:
                flush(buffer, p.page, current_heading)
            buffer.append(line)
        if in_checklist:
            flush(buffer, p.page, in_checklist, checklist=True)
        else:
            flush(buffer, p.page, current_heading)
    parent = None
    previous_number = ()
    body_started = False  # a method-side or abstract section has been seen
    for i, chunk in enumerate(result):
        heading = chunk["section"]
        number = section_number(heading)
        role = heading_role(heading)
        basis = "heading" if role != "unknown" else "none"
        if role == "unknown" and chunk.get("chapter"):
            # Bookmark outline beats a missed text heading: the nearest outline ancestor with a known role
            # ('II.9.2 Setting' under 'II.9 Research Methods') supplies it.
            path = (chunk.get("chapter_path") or chunk["chapter"]).split(" / ")
            role = next((r for t in reversed(path) if (r := heading_role(t)) != "unknown"), "unknown")
            basis = "bookmark" if role != "unknown" else basis
        warnings = []
        if (chunk["page"], heading) in unconfirmed:
            warnings.append("named as a checklist but no questionnaire follows; kept as protocol text")
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
        if top and top != (result[i - 1].get("chapter_top") if i else None):
            # A new top-level outline chapter replaces the parent context, also when its title has no known
            # role: the previous chapter (often 'Background') has ended.
            parent = {"heading": top, "role": heading_role(top), "number": section_number(top)}
        if parent and number and parent["number"] and number[0] != parent["number"][0]:
            # A numbered section outside the parent's chapter ('3.1' after '1. Background'): the chapter
            # heading in between was missed, so the old parent must not impose its role.
            parent = None
        if heading and (
            len(number) == 1 or (not number and (major_unnumbered or role != "unknown")) or parent is None
        ):
            parent = {"heading": heading, "role": role, "number": number}
        inherited = parent["role"] if parent else "unknown"
        if chunk.get("checklist"):
            role, basis = "checklist", "page_type"
            parent = {"heading": heading, "role": role, "number": ()}
        elif chunk.get("toc"):
            role, basis = "contents", "page_type"
            parent = None
        elif (
            inherited in {"references", "administrative", "background"}
            and len(number) != 1
            and basis != "bookmark"  # the outline places this section; a text parent does not override it
            # A background chapter may hold this study's own objectives or methods ('7.4 Research question and
            # objectives' in '7 Rationale and background'); sections of a reference list never do.
            and not (inherited == "background" and role in METHOD_ROLES)
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
        if role == "references" and len(chunk["text"]) >= 300 and not citation_like(chunk["text"]):
            # A references heading carried over code tables or a next chapter it did not detect.
            role, basis = "unknown", "content"
            chunk["role"], chunk["role_basis"] = role, basis
        body_started = body_started or role in METHOD_ROLES or role == "abstract"
        chunk["relevant"] = role not in NON_METHOD_ROLES and (
            role != "unknown"
            or chunk["relevant"]
            or bool(expand(chunk["text"][:1900])["concepts"])
            # Inside the protocol body, an unnamed subsection ('5.5.1 Primary endpoint' under '5 Procedures')
            # is read unless its chapter is background, administration or references.
            or (body_started and chunk["context_role"] not in NON_METHOD_ROLES)
        )
    headed = [c for c in result if c["section"] and not c.get("toc") and not c.get("checklist")]
    if not english(pages) or (headed and sum(c["role"] == "unknown" for c in headed) > 0.6 * len(headed)):
        # The role and signal words are English: when they recognise few of the document's headings (another
        # language or an unusual template), an unknown section is read rather than dropped for lack of them.
        for c in result:
            if c["role"] == "unknown":
                c["relevant"] = True
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
    # The appendix filter needs the PDF's own bookmarks: an outline read from a contents page may miss the
    # appendices' structure, so those PDFs read every relevant section as before.
    if not any(c.get("outline_source") == "bookmarks" for c in chunks):
        return relevant
    body = [c for c in relevant if c["role"] != "appendix"]
    # Any protocol text may cite an appendix ('the protocols attached as Appendix I' in a preamble), but not
    # the appendices, references, checklists or contents pages themselves.
    citing = [c for c in chunks if c["role"] not in {"appendix", "references", "checklist", "contents"}]
    referenced = {
        m.group(1).casefold() for c in citing for m in APPENDIX_REF.finditer(c["text"]) if m.group(1)
    }

    def is_referenced(chunk) -> bool:
        # The whole outline path: a sub-bookmark ('12.3.2 Terms for outcome mapping') belongs to its annex.
        label = " ".join(
            filter(None, (chunk.get("chapter_path") or chunk.get("chapter"), chunk.get("section")))
        )
        chapter = chunk.get("chapter") or ""
        if "checklist" in chapter.casefold():
            return False  # ENCePP checklists mimic method headings but are not this study's methods
        # Code lists and variable definitions are read even without an explicit cross-reference; the title may
        # be any level of the outline ('Appendix I: Definitions of study outcomes / ... / Staging').
        if CODE_LIST_TITLE.search(chunk.get("chapter_path") or chapter):
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
