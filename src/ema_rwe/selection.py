"""Explicit cohort filters and untruncated candidate accounting."""

from collections import Counter
from typing import Literal

from pydantic import Field

from .domain import Model
from .vocabulary import canonical, contains

# Catalogue narrowing categories. "others" is every study outside the claims/ehr/registry exports.
SourceType = Literal["claims", "ehr", "registry", "others"]
TYPED_SOURCES = ("claims", "ehr", "registry")
Design = Literal["case-control", "cohort", "cross-sectional", "ecological", "self-controlled"]


class SearchFilters(Model):
    countries: list[str] = Field(default_factory=list, max_length=30)
    data_source_types: list[SourceType] = Field(default_factory=list, max_length=4)
    study_designs: list[Design] = Field(default_factory=list, max_length=5)


SOURCE_ALIASES = {
    "claims": ["claims", "administrative healthcare records", "administrative health records"],
    "registry": ["registry", "registries"],
    "ehr": [
        "ehr",
        "electronic health records",
        "electronic healthcare records",
        "electronic medical records",
    ],
    "drug_dispensing_prescription": [
        "drug dispensing",
        "prescription",
        "prescriptions",
        "dispensing records",
    ],
}
DESIGN_ALIASES = {
    "case-control": ["case control"],
    "cohort": ["cohort"],
    "cross-sectional": ["cross sectional"],
    "ecological": ["ecological"],
    "self-controlled": ["self controlled", "sccs", "case crossover"],
}
# Japanese names and common English short forms for the countries with the most studies in the
# committed catalogue; values are canonical catalogue spellings. Other names pass through unchanged.
COUNTRY_NAMES = {
    "united kingdom": ["英国", "イギリス", "uk", "great britain"],
    "germany": ["ドイツ"],
    "spain": ["スペイン"],
    "france": ["フランス"],
    "united states": ["米国", "アメリカ", "us", "usa", "united states of america"],
    "italy": ["イタリア"],
    "netherlands": ["オランダ"],
    "denmark": ["デンマーク"],
    "sweden": ["スウェーデン"],
    "belgium": ["ベルギー"],
    "austria": ["オーストリア"],
    "norway": ["ノルウェー"],
    "finland": ["フィンランド"],
    "greece": ["ギリシャ"],
    "portugal": ["ポルトガル"],
    "canada": ["カナダ"],
    "poland": ["ポーランド"],
    "switzerland": ["スイス"],
    "ireland": ["アイルランド"],
    "japan": ["日本"],
    "hungary": ["ハンガリー"],
    "czechia": ["チェコ", "czech republic"],
    "australia": ["オーストラリア"],
    "croatia": ["クロアチア"],
    "korea republic of": ["韓国", "south korea", "korea"],
    "romania": ["ルーマニア"],
    "bulgaria": ["ブルガリア"],
    "china": ["中国"],
    "israel": ["イスラエル"],
    "taiwan": ["台湾"],
}
COUNTRIES = {alias: name for name, aliases in COUNTRY_NAMES.items() for alias in aliases}


def country(value):
    value = canonical(value)
    return COUNTRIES.get(value, value)


def categories(values, aliases):
    return sorted(key for key, names in aliases.items() if any(contains(v, n) for v in values for n in names))


def source_categories(values):
    """Catalogue narrowing categories; anything outside the typed exports counts as others."""
    return [c for c in categories(values, SOURCE_ALIASES) if c in TYPED_SOURCES] or ["others"]


def attributes(row):
    return {
        "countries": sorted({country(c) for c in row["countries"]}),
        "data_source_types": source_categories(row["data_source_types"]),
        "study_designs": categories(row.get("study_designs", []), DESIGN_ALIASES),
    }


def filter_rows(rows, filters):
    filters = filters or SearchFilters()
    requested = filters.model_dump()
    requested["countries"] = [country(c) for c in filters.countries]
    return [
        r
        for r in rows
        if all(not values or set(values) & set(attributes(r)[key]) for key, values in requested.items())
    ]


COMPACT_KEYS = (
    "study_id",
    "title",
    "status",
    "countries",
    "data_source_types",
    "study_designs",
    "conditions",
    "score",
    "analysis_available",
    "protocol_data_sources_status",
    "match_basis",
    "matched_terms",
    "matched_term_sources",
    "rank_features",
    "protocol_available",
    "protocol_listed",
    "protocol_found",
    "protocol_text_layer",
    "out_of_scope",
)


def compact(row):
    """Screening view of a candidate: enough to choose, without descriptions or provenance fields."""
    return {key: row.get(key) for key in COMPACT_KEYS if key in row}


def selection(rows, max_screening_studies=5, max_listed_candidates=50):
    counts = {key: Counter() for key in ("countries", "data_source_types", "study_designs")}
    unknown = dict.fromkeys(counts, 0)
    conditions = Counter()
    for row in rows:
        for key, values in attributes(row).items():
            counts[key].update(values)
            if key != "data_source_types":  # always non-empty: untyped studies count as others
                unknown[key] += not values
        unknown["data_source_types"] += not row["data_source_types"]
        conditions.update(canonical(c) for c in row.get("conditions", []))
    total = len(rows)
    listed = total <= max_listed_candidates
    return {
        "total_matches": total,
        "status": "needs_narrowing" if total > max_screening_studies else "ready" if total else "no_matches",
        "max_screening_studies": max_screening_studies,
        "facets": {
            **{key: dict(values) for key, values in counts.items()},
            "conditions": dict(conditions.most_common(max_listed_candidates)),
        },
        "unknown_metadata_counts": unknown,
        # Listing lets the user pick study_ids; above the limit only facets are returned.
        "candidates": [compact(r) for r in rows] if listed else [],
        "candidates_listed": listed,
        "max_listed_candidates": max_listed_candidates,
        "next_action": "Ask the user for a catalogue source type (claims, ehr, registry, others) AND study "
        "countries, showing the facets counts; conditions, study design or a narrower role-scoped query "
        "may narrow further. Pass the answers as filters. When candidates are listed, the user may instead "
        "pick study_ids within max_screening_studies. Do not silently select a subset."
        if total > max_screening_studies
        else "Process ALL matching studies using compare_protocols; do not pick one representative."
        if total
        else "Review English synonyms/codes and imported catalogue coverage; no local candidates matched.",
        "count_scope": "Distinct locally indexed candidate studies before result limiting; protocol availability and semantic relevance not yet verified.",
    }
