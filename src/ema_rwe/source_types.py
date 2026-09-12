"""Deterministic presentation of evidence-backed, role-specific source preferences."""

from .domain import RWEError, SourcePreference
from .selection import SOURCE_ALIASES, categories


def preference_for(filters, preference):
    legacy = filters.data_source_types if filters else []
    if legacy and preference:
        raise RWEError(
            "INVALID_INPUT", "Use source_preference or legacy filters.data_source_types, not both."
        )
    return preference or (SourcePreference(types=legacy, mode="only") if legacy else None)


def assess_row(row, preference):
    assessments = []
    for container in (row.get("analysis", {}), row.get("answer", {})):
        for item in container.get("source_assessments", []):
            if item not in assessments:
                assessments.append(item)
    # A study may have several outcomes. General extraction alone cannot establish that
    # the requested definition uses this type. Rank only the question-specific evidence.
    relevant = [
        a
        for a in row.get("answer", {}).get("source_assessments", [])
        if not preference or preference.role in {"any", a["role"]}
    ]
    usable = [a for a in relevant if a["usage"] in {"used", "planned"} and a["role"] != "unclear"]
    explicit = [a for a in usable if a["basis"] == "explicit"]
    requested = set(preference.types) if preference else set()
    matching = [a for a in explicit if requested.intersection(a["types"])]
    # Different outcomes in the same database can legitimately use different types.
    # Compare only the same named source, role AND definition, keeping all other passages visible.
    conflicts = [
        [a, b]
        for i, a in enumerate(explicit)
        for b in explicit[i + 1 :]
        if a["value"] == b["value"]
        and a["role"] == b["role"]
        and a["definition"] == b["definition"]
        and (
            a["requires_linkage"] != b["requires_linkage"]
            or (
                not set(a["types"]).intersection(b["types"])
                and not (a["requires_linkage"] or b["requires_linkage"])
            )
        )
    ]
    if not preference:
        status = "not_requested"
    elif row.get("status") != "complete":
        status = "unknown"
    elif conflicts:
        status = "conflicting"
    elif any(not a["requires_linkage"] for a in matching):
        status = "matched"
    elif matching:
        status = "partial"
    elif any(requested.intersection(a["types"]) for a in usable):
        status = "possible"
    elif explicit:
        status = "other_type"
    else:
        status = "unknown"
    protocol_types = sorted({t for a in assessments if a["basis"] == "explicit" for t in a["types"]})
    catalogue = categories(row["study"]["data_source_types"], SOURCE_ALIASES)
    return {
        "status": status,
        "role": preference.role if preference else "any",
        "assessments": assessments,
        "relevant_assessments": relevant,
        "conflicts": conflicts,
        "catalogue_protocol_disjoint": bool(
            catalogue and protocol_types and not set(catalogue).intersection(protocol_types)
        ),
        "reason": {
            "not_requested": "No source preference was requested.",
            "matched": "An explicit planned/used source supports this definition role without stated linkage dependency; check the actual algorithm.",
            "partial": "The preferred type contributes, but the definition also requires linked data.",
            "possible": "The preferred type is inferred from descriptive passages; it is not an explicit classification.",
            "other_type": "Available explicit evidence for this role describes other types; this is not proof that the preferred type is absent.",
            "unknown": "Processing, type, usage or definition role remains unconfirmed.",
            "conflicting": "Source/role classifications disagree; inspect the preserved evidence.",
        }[status],
    }


PRIORITY = {
    "matched": 0,
    "partial": 1,
    "possible": 2,
    "other_type": 3,
    "unknown": 4,
    "conflicting": 4,
    "not_requested": 0,
}


def select_rows(result, requested_ids=None):
    preference = (
        SourcePreference.model_validate(result["search"]["source_preference"])
        if result["search"].get("source_preference")
        else None
    )
    for row in result["rows"]:
        row["source_suitability"] = assess_row(row, preference)
    # Stable within each evidence group; neither BM25 nor an arbitrary top N is a selection decision.
    ranked = sorted(result["rows"], key=lambda r: PRIORITY[r["source_suitability"]["status"]])
    eligible = [
        r
        for r in ranked
        if not preference or preference.mode != "only" or r["source_suitability"]["status"] == "matched"
    ]
    eligible_ids = {r["study"]["study_id"] for r in eligible}
    limit = result["search"].get("max_comparison_studies", 5)
    if requested_ids is not None:
        if (
            not requested_ids
            or len(requested_ids) != len(set(requested_ids))
            or len(requested_ids) > limit
            or not set(requested_ids) <= eligible_ids
        ):
            raise RWEError("INVALID_INPUT", "Select unique eligible Study IDs within the comparison limit.")
        if result.get("status") != "complete":
            raise RWEError(
                "INCOMPLETE_SCREENING", "Finish all screening before selecting a comparison subset."
            )
        result["selected_study_ids"] = requested_ids
    saved = result.get("selected_study_ids")
    if saved and not set(saved) <= eligible_ids:
        result.pop("selected_study_ids", None)
        saved = None
    if saved:
        shown = [r for r in eligible if r["study"]["study_id"] in saved]
    elif len(eligible) <= limit:
        shown = eligible
    else:
        shown = []
    result["selection_status"] = (
        "screening_incomplete"
        if result.get("status") != "complete"
        else "needs_selection"
        if len(eligible) > limit and not saved
        else "no_confirmed_matches"
        if not eligible
        else "ready"
    )
    result["ranked_study_ids"] = [r["study"]["study_id"] for r in ranked]
    result["displayed_study_ids"] = [r["study"]["study_id"] for r in shown]
    result["eligible_study_ids"] = [r["study"]["study_id"] for r in eligible]
    result["screening_summary"] = [
        {
            "study_id": r["study"]["study_id"],
            "title": r["study"]["title"],
            "status": r["status"],
            "error": r.get("error"),
            "suitability": r["source_suitability"]["status"],
            "reason": r["source_suitability"]["reason"],
            "in_comparison": r["study"]["study_id"] in result["displayed_study_ids"],
        }
        for r in ranked
    ]
    return shown
