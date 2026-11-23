"""The evidence store (spec v1.31): the facts of saved analyses read one row per fact, facts distributed
without their quotes (prebuilt_facts) and those quotes restored from a local PDF of the same SHA-256."""

import hashlib
import html
import re
from importlib.metadata import version
from itertools import groupby
from pathlib import Path
from typing import Literal

from .domain import RWEError, Study, atomic_write, now
from .ema import BASE, is_non_interventional
from .pdf import PARSER_VERSION, normalize_quote, page_texts, protocol_locations, reading_hash
from .selection import source_categories
from .storage import Repository

# Extraction (schema 0.3) field path -> item kind. source_assessments are left out: they rank PDF evidence
# for a source preference and repeat the data sources. missing_information becomes 'missing' rows.
FACT_FIELDS = (
    ("study_design", "study_design"),
    ("cohort.inclusion_criteria", "cohort.inclusion"),
    ("cohort.exclusion_criteria", "cohort.exclusion"),
    ("cohort.index_date", "cohort.index_date"),
    ("cohort.baseline_period", "cohort.baseline_period"),
    ("cohort.follow_up", "cohort.follow_up"),
    ("cohort.design_schema.time_windows", "cohort.time_window"),
    ("population", "population"),
    ("exposure", "exposure"),
    ("comparator", "comparator"),
    ("outcomes", "outcome"),
    ("data_sources", "data_source"),
    ("disease_definitions", "disease_definition"),
    ("statistical_analysis", "statistical_analysis"),
    ("key_notes", "key_note"),
)
FACT_KINDS = (*(kind for _, kind in FACT_FIELDS), "missing")
# Location rows (spec v1.33): where a kept PDF lists codes and keeps its chapters, built from the parse alone.
# Returned only when asked for by item_kinds.
LOCATION_KINDS = ("code_table", "section_map")
ITEM_KINDS = (*FACT_KINDS, *LOCATION_KINDS)
# Version of protocol_locations' rules: a stored result of another version is built again.
LOCATIONS_VERSION = 1
ItemKind = Literal[ITEM_KINDS]
MAX_FACTS = 1000
QUOTE_NOTE = "The quote is shown once this PDF is kept locally (get_protocol)."
NO_POSITION = dict.fromkeys(("page", "section", "quote_start", "quote_end", "quote_sha256", "quote_length"))


def _at(analysis: dict, path: str) -> list[dict]:
    node = analysis
    for key in path.split("."):
        node = (node or {}).get(key)
    return node if isinstance(node, list) else [node] if node else []


def _fact(source: dict, kind: str, value: str, usage, evidence: list[dict], status: str) -> dict:
    row = {
        "study_id": source["study_id"],
        "protocol_id": source["protocol_id"],
        "pdf_sha256": source["pdf_sha256"],
        "item_kind": kind,
        "value": value,
    }
    if kind == "data_source":
        row["usage"] = usage  # planned, used, candidate or unclear: never merged
    return row | {
        "evidence": evidence,
        "status": status if evidence else "no_quote",
        "extractor": source["extractor"],
        "parser_version": source["parser_version"],
        "app_version": source["app_version"],
    }


def facts_from_analysis(result: dict) -> list[dict]:
    """One row per fact of a saved analysis, derived on every read and never stored apart from it, so a
    retired analysis takes its facts with it."""
    source, analysis = result["source"], result["analysis"]
    source = {
        "study_id": source["study_id"],
        "protocol_id": source.get("protocol_id"),
        "pdf_sha256": source.get("sha256"),
        "extractor": source.get("extractor"),
        "parser_version": source.get("parser_version"),
        "app_version": source.get("app_version"),
    }
    rows = [
        _fact(
            source,
            kind,
            fact["value"],
            fact.get("usage"),
            [
                {"page": e["page"], "section": e.get("section"), "quote": e["quote"]}
                | ({"server": e["server"]} if e.get("server") else {})
                for e in fact["evidence"]
            ],
            "verified_local",
        )
        for path, kind in FACT_FIELDS
        for fact in _at(analysis, path)
    ]
    return rows + [
        _fact(source, "missing", text, None, [], "no_quote")
        for text in analysis.get("missing_information", [])
    ]


def location_rows(study_id: str, locations: dict) -> list[dict]:
    """find_facts rows of a protocol's stored locations: code_table and section_map, each quoting the line
    that names its section on its first page."""
    protocol_id = locations["protocol_id"]
    rows = []
    for kind, key in (("code_table", "code_tables"), ("section_map", "section_map")):
        for loc in locations[key]:
            detail = ("kind", "systems", "systems_inferred") if kind == "code_table" else ("role",)
            rows.append(
                {
                    "study_id": study_id,
                    "protocol_id": protocol_id,
                    "pdf_sha256": protocol_id.rsplit("_", 1)[1],
                    "item_kind": kind,
                    "value": loc["value"],
                    "page": loc["page"],
                    "end_page": loc["end_page"],
                    "section_id": loc["section_id"],
                    **{k: loc[k] for k in detail},
                    "evidence": [{"page": loc["page"], "section": loc["section"], "quote": loc["quote"]}],
                    "status": "verified_local" if loc["quote"] else "no_quote",
                    "extractor": "structure",
                    "parser_version": locations["parser_version"],
                    "app_version": locations["app_version"],
                }
            )
    return rows


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def quote_position(page_text: str, quote: str) -> dict | None:
    """Where quote lies in the page text, both normalized as evidence is checked (pdf.normalize_quote): start
    and end in the normalized text, and the SHA-256 and length of the normalized quote. None if absent."""
    text, wanted = normalize_quote(page_text), normalize_quote(quote)
    start = text.find(wanted) if wanted else -1
    if start < 0:
        return None
    return {
        "quote_start": start,
        "quote_end": start + len(wanted),
        "quote_sha256": _digest(wanted),
        "quote_length": len(wanted),
    }


def _normalized_with_origin(text: str) -> tuple[str, list[int]]:
    """normalize_quote(text), and for each of its characters the index in text it came from."""
    kept: list[str] = []
    origin: list[int] = []
    space = None  # where an unwritten whitespace run began
    for i, char in enumerate(text):
        if char == "\u00ad":
            continue
        if char.isspace():
            space = i if space is None else space
            continue
        if space is not None and kept and kept[-1] != "-":  # a hyphen joins across a line break
            kept.append(" ")
            origin.append(space)
        space = None
        for folded in char.casefold():
            kept.append(folded)
            origin.append(i)
    return "".join(kept), origin


def restore_quote(page_text: str, position: dict) -> str | None:
    """The quote a position stands for, cut verbatim from the page text: at its place when the hash agrees,
    else at the first place of the same length on the page that does (a parser change may shift text). None
    when nowhere on the page."""
    text, length, digest = normalize_quote(page_text), position["quote_length"], position["quote_sha256"]
    if length < 1:  # an empty quote matches everywhere: no evidence
        return None
    for start in (position["quote_start"], *range(len(text) - length + 1)):
        if _digest(text[start : start + length]) == digest:
            kept, origin = _normalized_with_origin(page_text)
            # Should the two normalizations ever disagree, the normalized quote is still the verified one
            if kept != text:
                return text[start : start + length]
            return " ".join(page_text[origin[start] : origin[start + length - 1] + 1].split())
    return None


def _prebuilt_facts(rows: list[dict]) -> list[dict]:
    """Fact rows from distributed rows (one per quote, a fact's quotes written one after another)."""
    facts = []
    for _, group in groupby(
        rows, key=lambda r: (r["study_id"], r["protocol_id"], r["item_kind"], r["value"], r["usage"])
    ):
        group = list(group)
        quoted = [r for r in group if r["page"] is not None]
        fact = _fact(
            group[0],
            group[0]["item_kind"],
            group[0]["value"],
            group[0]["usage"],
            [{"page": r["page"], "section": r["section"], "quote": ""} for r in quoted],
            "verified_at_build",
        )
        fact["_positions"] = quoted
        facts.append(fact)
    return facts


def _prebuilt_rows(fact: dict, positions: list[dict], built_at: str) -> list[dict]:
    row = {
        "pdf_sha256": fact["pdf_sha256"],
        "study_id": fact["study_id"],
        "protocol_id": fact["protocol_id"],
        "item_kind": fact["item_kind"],
        "value": fact["value"],
        "usage": fact.get("usage"),
        "extractor": fact["extractor"],
        "parser_version": fact["parser_version"],
        "app_version": fact["app_version"],
        "built_at": built_at,
    }
    if not fact["evidence"]:
        return [row | NO_POSITION]
    return [
        row | {"page": e["page"], "section": e["section"]} | position
        for e, position in zip(fact["evidence"], positions, strict=True)
    ]


class EvidenceMixin:
    """Service methods of the evidence store; uses repo and archive. Never reaches the network."""

    def _studies_of_types(self, source_types: list[str], with_facts: set[str]) -> list[str]:
        """Non-interventional studies of these catalogue source types that list a protocol or have facts."""
        with self.repo.connection() as db:
            bodies = [r[0] for r in db.execute("SELECT body FROM studies").fetchall()]
        wanted = set(source_types)
        return sorted(
            (
                s.study_id
                for s in map(Study.model_validate_json, bodies)
                if is_non_interventional(s.study_type)
                and (s.protocol_listed or s.study_id in with_facts)
                and wanted & set(source_categories(s.data_source_types))
            ),
            key=int,
        )

    def _restore(self, facts: list[dict]) -> None:
        """Give the distributed facts among these their quotes, from a kept PDF of the same SHA-256."""
        for protocol_id, group in groupby(
            (f for f in facts if "_positions" in f), key=lambda f: f["protocol_id"]
        ):
            group = list(group)
            try:
                pdf, _ = self.archive.load(protocol_id)  # checks the bytes against the ID's SHA-256
                texts = page_texts(pdf, {p["page"] for f in group for p in f["_positions"]})
            except RWEError:
                texts = None  # no such PDF here (or an unreadable one): the quotes stay unshown
            for fact in group:
                positions = fact.pop("_positions")
                quotes = [
                    restore_quote(texts[p["page"]], p) if texts and p["page"] in texts else None
                    for p in positions
                ]
                if positions and all(quotes):
                    for evidence, quote in zip(fact["evidence"], quotes, strict=True):
                        evidence["quote"] = quote
                    fact["status"] = "verified_local"
                elif positions:
                    fact["quote_note"] = QUOTE_NOTE

    def kept_locations(self, study_id: str, protocol_id: str) -> dict | None:
        """The code tables and chapter map of a kept protocol (spec v1.33): the stored ones when built from the
        same protocol, heading reading and rules, else built from the local PDF and stored with the study's
        protocol observation. None when the PDF is not kept here or has no readable text."""
        reading = reading_hash(self.explorer.translations(protocol_id))
        key = {"protocol_id": protocol_id, "reading": reading, "version": LOCATIONS_VERSION}
        stored = self.repo.observation(study_id).get("locations")
        if stored and all(stored.get(k) == v for k, v in key.items()):
            return stored
        try:
            pages, _, _ = self.explorer.context(protocol_id)
        except RWEError:
            return None  # not kept here, unreadable, or without a text layer
        locations = key | {"parser_version": PARSER_VERSION, "app_version": version("ema-rwe-mcp")}
        locations |= protocol_locations(pages)
        self.repo.observe(study_id, locations=locations)
        return locations

    def _location_rows(self, study_id: str, analysis: dict | None, build: bool) -> list[dict] | None:
        """Location rows of the study's analysed protocol, else of its latest observed one. Built only when
        build is set (a study asked for by ID); otherwise only stored ones are used. None when there are none."""
        protocol_id = (analysis or {}).get("source", {}).get("protocol_id")
        protocol_id = protocol_id or self.repo.observation(study_id).get("protocol_id")
        if not protocol_id:
            return None
        if build:
            locations = self.kept_locations(study_id, protocol_id)
        else:
            stored = self.repo.observation(study_id).get("locations")
            locations = stored if stored and stored.get("protocol_id") == protocol_id else None
        return location_rows(study_id, locations) if locations else None

    def find_facts(
        self,
        study_ids: list[str] | None = None,
        item_kinds: list[str] | None = None,
        source_types: list[str] | None = None,
        text: str | None = None,
        limit: int | None = 200,
    ) -> dict:
        """Fact rows of the studies asked for: study_ids, else the studies of source_types, else every study
        with facts. A study's facts come from its local analysis, else from the distributed facts; a study
        with neither is listed in no_facts. text matches a value or a quote shown before restoration (the
        quotes of distributed facts are restored for the returned rows only).

        Location rows (code_table, section_map) come only when item_kinds asks for them, from the study's
        kept PDF: built on first use for the study_ids asked for, stored ones only for wider scopes. Studies
        without them are listed in no_locations."""
        if limit is not None and not 1 <= limit <= MAX_FACTS:
            raise RWEError("INVALID_INPUT", f"limit must be 1..{MAX_FACTS}.")
        if any(not re.fullmatch(r"\d{1,20}", s) for s in study_ids or []):
            raise RWEError("INVALID_INPUT", "study_ids must be numeric Study IDs.")
        local, prebuilt = self.repo.analysed_studies(), self.repo.prebuilt_studies()
        if study_ids:
            scope = list(dict.fromkeys(study_ids))
        elif source_types:
            scope = self._studies_of_types(source_types, local | prebuilt)
        else:
            scope = sorted(local | prebuilt, key=int)
        analyses = self.repo.analyses([s for s in scope if s in local])
        distributed: dict[str, list[dict]] = {}
        for fact in _prebuilt_facts(self.repo.prebuilt_facts([s for s in scope if s in prebuilt - local])):
            distributed.setdefault(fact["study_id"], []).append(fact)
        needle = text.casefold() if text else None
        kinds = set(item_kinds or FACT_KINDS)
        wanted_locations = kinds & set(LOCATION_KINDS)
        origins, found, no_locations = {}, [], []
        for study_id in scope:
            if study_id in analyses:
                origins[study_id], facts = "local", facts_from_analysis(analyses[study_id])
            else:
                origins[study_id] = "prebuilt" if study_id in distributed else "none"
                facts = distributed.get(study_id, [])
            found += [
                f
                for f in facts
                if f["item_kind"] in kinds
                and (
                    needle is None
                    or needle in f["value"].casefold()
                    or any(needle in e["quote"].casefold() for e in f["evidence"])
                )
            ]
            if wanted_locations:
                rows = self._location_rows(study_id, analyses.get(study_id), build=bool(study_ids))
                if rows is None:
                    no_locations.append(study_id)
                found += [
                    r
                    for r in rows or []
                    if r["item_kind"] in kinds
                    and (
                        needle is None
                        or needle in r["value"].casefold()
                        or needle in r["evidence"][0]["quote"].casefold()
                    )
                ]
        returned = found if limit is None else found[:limit]
        self._restore(returned)
        return {
            "facts": returned,
            "total_facts": len(found),
            "truncated": len(returned) < len(found),
            "origins": origins,
            "no_facts": [s for s, origin in origins.items() if origin == "none"],
            **({"no_locations": no_locations} if wanted_locations else {}),
            "network_requests": 0,
        }

    def export_facts(self, output: Path, source_type: str | None = None) -> dict:
        """Distributed facts (prebuilt_facts) from the local analyses and their kept PDFs, written into the
        database at output, replacing the rows it holds for the same studies. A fact with a quote that cannot
        be placed on its page is left out and counted."""
        analysed = self.repo.analysed_studies()
        studies = (
            self._studies_of_types([source_type], analysed) if source_type else sorted(analysed, key=int)
        )
        built_at, rows, exported = now(), [], set()
        summary = {"output": str(output), "studies": 0, "facts_written": 0, "facts_without_position": 0}
        summary["studies_without_pdf"] = []
        for study_id, result in self.repo.analyses([s for s in studies if s in analysed]).items():
            try:
                pdf, _ = self.archive.load(result["source"]["protocol_id"])
            except RWEError:
                summary["studies_without_pdf"].append(study_id)
                continue
            facts = facts_from_analysis(result)
            texts = page_texts(pdf, {e["page"] for f in facts for e in f["evidence"]})
            exported.add(study_id)
            summary["studies"] += 1
            for fact in facts:
                positions = [quote_position(texts.get(e["page"], ""), e["quote"]) for e in fact["evidence"]]
                if not all(positions):
                    summary["facts_without_position"] += 1
                    continue
                rows += _prebuilt_rows(fact, positions, built_at)
                summary["facts_written"] += 1
        Repository(output).replace_prebuilt_facts(exported, rows)
        return summary | {"rows_written": len(rows)}

    def write_facts_site(self, output: Path, **filters) -> dict:
        """One self-contained, read-only HTML page of find_facts' rows (nothing loaded from elsewhere)."""
        found = self.find_facts(**filters)
        titles = {s: (study.title if (study := self.repo.get(s)) else "") for s in found["origins"]}
        atomic_write(output, facts_page(found, titles, now()))
        return {
            "output": str(output),
            "studies": len({fact["study_id"] for fact in found["facts"]}),
            "facts": len(found["facts"]),
            "truncated": found["truncated"],
            "no_facts": len(found["no_facts"]),
        }


STYLE = (
    "body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:auto;padding:0 1rem;color:#222;"
    "background:#fff}h2{border-top:1px solid #ccc;padding-top:1rem}blockquote{margin:.2rem 0 .6rem 1rem;"
    "padding-left:.6rem;border-left:3px solid #ccc;color:#444}.meta{color:#666;font-size:13px}"
    "@media (prefers-color-scheme:dark){body{background:#111;color:#ddd}blockquote{color:#bbb}}"
)


def facts_page(found: dict, titles: dict[str, str], generated_at: str) -> str:
    """The HTML of write_facts_site. Values and quotes are PDF-derived, untrusted text: every one is escaped."""
    e = html.escape
    by_study: dict[str, list[dict]] = {}
    for fact in found["facts"]:
        by_study.setdefault(fact["study_id"], []).append(fact)
    parts = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>EMA RWE facts</title><style>{STYLE}</style></head><body>",
        "<h1>保存済みの事実</h1>",
        f'<p class="meta">{e(generated_at)} 作成。事実 {len(found["facts"])} 件（該当 {found["total_facts"]} 件）。</p>',
    ]
    for study_id, facts in by_study.items():
        url = f"{BASE}/study/{study_id}"
        parts.append(
            f'<section><h2><a href="{e(url)}">{e(study_id)}</a> {e(titles.get(study_id, ""))}</h2>'
            f'<p class="meta">出所：{e(found["origins"][study_id])}</p>'
        )
        for kind, group in groupby(facts, key=lambda f: f["item_kind"]):
            parts.append(f"<h3>{e(kind)}</h3><ul>")
            for fact in group:
                usage = f" <span class=meta>[{e(fact['usage'])}]</span>" if fact.get("usage") else ""
                parts.append(f"<li>{e(fact['value'])}{usage}")
                for evidence in fact["evidence"]:
                    where = f"p. {evidence['page']}" + (
                        f" / {e(evidence['section'])}" if evidence["section"] else ""
                    )
                    quote = e(evidence["quote"]) if evidence["quote"] else "引用は PDF 取得後に表示"
                    parts.append(f'<div class="meta">{where}</div><blockquote>{quote}</blockquote>')
                parts.append("</li>")
            parts.append("</ul>")
        parts.append("</section>")
    if found["no_facts"]:
        parts.append(f"<section><h2>事実のない研究</h2><p>{e(', '.join(found['no_facts']))}</p></section>")
    return "\n".join(parts) + "\n</body></html>\n"
