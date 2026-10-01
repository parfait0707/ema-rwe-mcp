"""Tiered screening retrieval: concept blocks searched in every column, ranked by how specifically and
where each study matched. On the quasi-gold set (docs/validation.md, 2026-09-28, provisional labels)
compare_protocols reached relative recall 0.965 (role-filtered retrieval 0.858) and 4.10 relevant
studies in the top 5 (fused rank alone 3.60)."""

from typing import Literal

from pydantic import Field, field_validator

from .domain import Model

RRF_K = 20  # fusion constant used by TrialGPT for per-keyword BM25 lists


class ScreeningBlock(Model):
    """One concept of the question: OR within the block, AND across blocks."""

    role: Literal["any", "outcome", "condition", "exposure"] = "any"
    queries: list[str] = Field(min_length=1, max_length=20)
    category_terms: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Umbrella terms a record may use instead of the specific name (ICD-10 block or chapter "
        "title, MACE/AESI-style composite); the server adds medicine classes itself. Ranked below specific matches",
    )

    @field_validator("queries", "category_terms")
    @classmethod
    def no_blank_terms(cls, terms: list[str]) -> list[str]:
        # A blank term would match the whole catalogue (an empty query returns every study).
        if any(not t.strip() or len(t) > 500 for t in terms):
            raise ValueError("terms must be nonempty and at most 500 characters")
        return terms


def type_fit(row: dict) -> int:
    """+1 for secondary-use data (a catalogue data source type), -1 for a survey or cross-sectional design:
    such studies rarely define outcomes with codes."""
    designs = " ".join(row.get("study_designs", [])).casefold()
    survey = "survey" in row.get("title", "").casefold() or "cross-sectional" in designs
    return int(bool(row.get("data_source_types"))) - int(survey)


def rank_block(search, block: ScreeningBlock) -> tuple[dict[str, dict], dict[str, float], set[str], set[str]]:
    """Rows, fused scores, studies matched by a specific query, and those matched in the role's columns.

    `search(term, role, expand)` returns ranked catalogue rows; `expand` says whether the caller's
    synonyms and codes apply (they do for specific queries, never for category terms).
    """
    rows: dict[str, dict] = {}
    scores: dict[str, float] = {}
    specific: set[str] = set()
    in_role: set[str] = set()

    def add(ranked: list[dict], tier: str) -> None:
        for rank, row in enumerate(ranked, 1):
            kept = rows.setdefault(row["study_id"], {**row, "matched_terms": [], "matched_term_sources": {}})
            kept["matched_terms"] = list(dict.fromkeys(kept["matched_terms"] + row["matched_terms"]))
            sources = {t: s for t, s in row["matched_term_sources"].items() if tier == "specific"}
            sources |= {t: "category" for t in row["matched_terms"] if tier == "category"}
            kept["matched_term_sources"] = {**sources, **kept["matched_term_sources"]}
            scores[row["study_id"]] = scores.get(row["study_id"], 0.0) + 1 / (RRF_K + rank)

    for query in block.queries:
        ranked = search(query, "any", True)
        add(ranked, "specific")
        specific |= {r["study_id"] for r in ranked}
        if block.role != "any":
            in_role |= {r["study_id"] for r in search(query, block.role, True)}
    for term in block.category_terms:
        add(search(term, "any", False), "category")
    return rows, scores, specific, in_role if block.role != "any" else set(specific)


def screen(search, blocks: list[ScreeningBlock]) -> list[dict]:
    """Candidates present in every block, most promising first. Nothing is dropped: the order only
    decides what the user sees first (specific > category-only, role columns > elsewhere, study type)."""
    ranked = [rank_block(search, block) for block in blocks]
    common = set(ranked[0][0]).intersection(*(r[0] for r in ranked[1:]))
    out = []
    for study_id in common:
        row = dict(ranked[0][0][study_id])
        for rows, *_ in ranked[1:]:
            other = rows[study_id]
            row["matched_terms"] = list(dict.fromkeys(row["matched_terms"] + other["matched_terms"]))
            # A term found by a specific query in any block is reported as specific, not category.
            merged = {**other["matched_term_sources"], **row["matched_term_sources"]}
            for term, source in other["matched_term_sources"].items():
                if merged[term] == "category" and source != "category":
                    merged[term] = source
            row["matched_term_sources"] = merged
        row["rank_features"] = {
            "specific_blocks": sum(study_id in r[2] for r in ranked),
            "role_blocks": sum(study_id in r[3] for r in ranked),
            "type_fit": type_fit(row),
            "fused_score": round(sum(r[1][study_id] for r in ranked), 4),
            "blocks": len(blocks),
        }
        out.append(row)
    out.sort(key=order_key)
    return out


def order_key(row: dict) -> tuple:
    f = row["rank_features"]
    available = row.get("protocol_available")
    return (
        available is False,  # a study without a published protocol cannot answer; keep it, but last
        -f["specific_blocks"],
        -f["role_blocks"],
        -f["type_fit"],
        -f["fused_score"],
        row["study_id"],
    )
