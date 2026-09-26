"""Parsing only: no I/O, resilient to layout changes within labelled fields."""

import re
from datetime import date
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .domain import Document, RWEError, Study

BASE = "https://catalogues.ema.europa.eu"


def norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def is_non_interventional(value: str) -> bool:
    return norm(value) in {"non interventional", "non interventional study"}


def fields(html: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for dl in soup.select("dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if dt and dd:
            items = dd.select(".field__item")
            result[norm(dt.get_text(" ", strip=True))] = [
                x.get_text(" ", strip=True) for x in (items or [dd])
            ]
    return result


def parse_study(html: str, study_id: str, methods: str = "", data: str = "") -> Study:
    f = fields(html) | fields(methods) | fields(data)

    def one(key):
        return " ".join(f.get(key, []))

    if one("study id") != study_id or not one("official title and acronym"):
        raise RWEError("STUDY_NOT_FOUND", "Study page has no matching labelled Study ID and title.")
    soup = BeautifulSoup(html, "html.parser")
    tabs = {
        key: urljoin(BASE, a["href"])
        for key in ("administrative-details", "methodological-aspects", "data-management", "study-documents")
        for a in soup.select("a[href]")
        if a["href"].rstrip("/").endswith("/" + key)
    }
    darwin = one("darwin eu study")
    return Study(
        study_id=study_id,
        title=one("official title and acronym"),
        eupas_number=one("eu pas number") or None,
        description=one("study description"),
        study_type=one("study type"),
        study_designs=f.get("non interventional study design", []),
        status=one("study status"),
        darwin_eu={"Yes": True, "No": False}.get(darwin),
        countries=f.get("study countries", []),
        data_source_types=f.get("data sources types", f.get("data source type", [])),
        catalogue_data_sources=f.get("data sources", f.get("data source s", [])),
        source_url=f"{BASE}/study/{study_id}",
        metadata_source="EMA labelled detail pages",
        tabs=tabs,
    )


def parse_date(text: str) -> str | None:
    patterns = [
        (r"(?<!\d)(20\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})(?!\d)", (1, 2, 3)),
        (r"(?<!\d)(\d{2})/(\d{2})/(20\d{2})(?!\d)", (3, 2, 1)),
    ]
    for pattern, order in patterns:
        for m in re.finditer(pattern, text):
            try:
                return date(*(int(m.group(i)) for i in order)).isoformat()
            except ValueError:
                continue
    return None


def parse_documents(html: str) -> list[Document]:
    soup = BeautifulSoup(html, "html.parser")
    docs = {}
    # Restrict to protocol fields, never infer from links in reports/publications.
    for container in soup.select('[class*="field-darwin-protocol-file"]'):
        cls = " ".join(container.get("class", []))
        kind = "updated" if "-upd" in cls else "initial"
        for a in container.select("a[href]"):
            url = urljoin(BASE, a["href"])
            if not unquote(url).split("?")[0].lower().endswith(".pdf"):
                continue
            card = a.find_parent(class_="bcl-file") or a.find_parent(class_="field__item")
            if card is None:
                continue
            title_el = card.select_one(".file-title")
            title = title_el.get_text(" ", strip=True) if title_el else unquote(url.rsplit("/", 1)[-1])
            version = re.search(r"(?:\bversion\s*|\bv|_v)(\d+(?:\.\d+)*)", title, re.IGNORECASE)
            metadata = card.select_one(".file-metadata")
            docs[url] = Document(
                title=title,
                document_url=url,
                kind=kind,
                version=version.group(1) if version else None,
                document_date=parse_date(title),
                published_date=parse_date(metadata.get_text(" ", strip=True)) if metadata else None,
            )
    return list(docs.values())


KIND_RANK = {"initial": 0, "updated": 1}


def select_protocol(docs: list[Document], version: str = "latest") -> tuple[Document, str]:
    if version != "latest":
        docs = [d for d in docs if (d.version or "").lower().lstrip("v") == version.lower().lstrip("v")]
    if not docs:
        raise RWEError("PROTOCOL_NOT_FOUND", "No matching protocol PDF in Study documents.")
    best_kind = max(KIND_RANK[d.kind] for d in docs)
    group = [d for d in docs if KIND_RANK[d.kind] == best_kind]
    if all(d.version for d in group):
        key = lambda d: (
            tuple(int(x) for x in d.version.split(".")) + (0,) * (8 - len(d.version.split("."))),
            d.document_date or "",
            d.published_date or "",
            d.document_url,
        )
        reason = "protocol category, numeric version, document date, publication date"
    elif all(d.document_date for d in group):
        key = lambda d: (d.document_date, d.published_date or "", d.document_url)
        reason = "protocol category, document date (not all versions available), publication date"
    else:
        key = lambda d: (d.published_date or d.document_date or "", d.document_url)
        reason = "protocol category, available dates; chronology uncertain: incomplete version/date metadata"
    return max(group, key=key), reason
