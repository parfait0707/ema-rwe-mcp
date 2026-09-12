"""Explicit cohort filters and untruncated candidate accounting."""

from collections import Counter
from typing import Literal

from pydantic import Field

from .domain import Model
from .vocabulary import canonical, contains

SourceType = Literal["claims", "registry", "ehr", "drug_dispensing_prescription"]
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
COUNTRIES = {
    "日本": "japan",
    "jp": "japan",
    "米国": "united states",
    "us": "united states",
    "usa": "united states",
    "uk": "united kingdom",
    "英国": "united kingdom",
}


def country(value):
    value = canonical(value)
    return COUNTRIES.get(value, value)


def categories(values, aliases):
    return sorted(key for key, names in aliases.items() if any(contains(v, n) for v in values for n in names))


def attributes(row):
    return {
        "countries": sorted({country(c) for c in row["countries"]}),
        "data_source_types": categories(row["data_source_types"], SOURCE_ALIASES),
        "study_designs": categories(row.get("study_designs", []), DESIGN_ALIASES),
    }


def filter_rows(rows, filters):
    filters = filters or SearchFilters()
    requested = filters.model_dump()
    # PDF assessment is deferred until bounded candidate screening. Missing catalogue types
    # must not exclude a study; legacy source filters are enforced in comparison output.
    requested.pop("data_source_types")
    requested["countries"] = [country(c) for c in filters.countries]
    return [
        r
        for r in rows
        if all(not values or set(values) & set(attributes(r)[key]) for key, values in requested.items())
    ]


def selection(rows, max_screening_studies=5):
    counts = {key: Counter() for key in ("countries", "data_source_types", "study_designs")}
    unknown = dict.fromkeys(counts, 0)
    for row in rows:
        for key, values in attributes(row).items():
            counts[key].update(values)
            unknown[key] += not values
    total = len(rows)
    return {
        "total_matches": total,
        "status": "needs_narrowing" if total > max_screening_studies else "ready" if total else "no_matches",
        "max_screening_studies": max_screening_studies,
        "facets": {key: dict(values) for key, values in counts.items()},
        "unknown_metadata_counts": unknown,
        "next_action": "Ask for country, study design or clinical conditions; source preference cannot exclude unassessed PDFs. Do not silently select a subset."
        if total > max_screening_studies
        else "Process ALL matching studies using compare_protocols; do not pick one representative."
        if total
        else "Review English synonyms/codes and imported catalogue coverage; no local candidates matched.",
        "count_scope": "Distinct locally indexed candidate studies before result limiting; protocol availability and semantic relevance not yet verified.",
    }
