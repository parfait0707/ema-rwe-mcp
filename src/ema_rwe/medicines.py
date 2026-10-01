"""Medicine search names from the catalogue's own ATC-coded exposures and the EMA medicines dictionary.

ATC is only the join key, never a lookup answer: the catalogue writes medicines as '(B01AF02) apixaban'
or '(N03A) ANTIEPILEPTICS', while over half of its studies name medicines without any code. Every
expansion is therefore searched as names. No WHO ATC index ships; class membership comes from the
codes the catalogue and the EMA dictionary actually carry.
"""

import re

from .drugs import drug_expansion, ingredient_set, load_dictionary
from .vocabulary import canonical

CODED = re.compile(r"^\(([A-Z]\d{2}(?:[A-Z]{1,2}(?:\d{2})?)?)\)\s*(.+)$")
# A typed class code must be 3rd/4th level: 2nd-level codes such as N03 are also ICD-10 categories.
TYPED = re.compile(r"[A-Z]\d{2}[A-Z]{1,2}(?:\d{2})?")
# ATC leaf names that mean something only under their parent code ('B05BA10 combinations').
GENERIC = re.compile(r"(?:combinations?|various|others?)", re.IGNORECASE)
MAX_NAMES = 100


def catalogue_atc(entries) -> dict[str, str]:
    """ATC code -> the catalogue's own name for it, from exposure entries such as '(N03A) ANTIEPILEPTICS'."""
    labels: dict[str, str] = {}
    for entry in entries:
        if match := CODED.match(entry.strip()):
            labels.setdefault(match[1], match[2].strip())
    return labels


def expand_medicine(query: str, labels: dict[str, str]) -> dict | None:
    """Names to search for a medicine or medicine class, or None when the query is neither.

    A medicine (5th-level code) adds its catalogue name as a specific term and its 4th-level class as a
    category term. A class adds its own name, then every member name the EMA dictionary or the catalogue
    codes under it, as specific terms: a member is part of the class, not a synonym of another member.
    Combination labels stay whole, so 'metformin' never resolves to 'metformin and empagliflozin', and a
    combination ('empagliflozin and metformin', any order) resolves only to that whole ingredient set.
    A typed code counts only when some known code starts with it (no non-ATC code of the same shape).
    """
    text = query.strip()
    records = load_dictionary()[0]
    known = {*labels, *(c for row in records for c in row["atc_codes"])}
    whole = ingredient_set(text)
    combination = len(whole) > 1
    codes = {
        c
        for c, label in labels.items()
        if label.casefold() == text.casefold() or (combination and ingredient_set(label) == whole)
    }
    if TYPED.fullmatch(text.upper()) and any(c.startswith(text.upper()) for c in known):
        codes.add(text.upper())
    matches = drug_expansion(text)["matches"]
    if combination:
        # Never resolve a combination through one of its ingredients.
        matches = [m for m in matches if frozenset(map(canonical, m["ingredients"])) == whole]
    codes |= {c for m in matches for c in m["atc_codes"]}
    if not codes:
        return None

    def name(code: str) -> str | None:
        label = labels.get(code)
        return label if label and not GENERIC.fullmatch(label) else None

    own: dict[str, str] = {}
    members: dict[str, str] = {}
    category: dict[str, str] = {}
    for code in sorted(codes):
        if len(code) == 7:
            if label := name(code):
                own.setdefault(label, "catalogue_atc")
            category |= {t: "catalogue_atc" for t in (code[:5], name(code[:5])) if t}
            continue
        if label := name(code):
            own.setdefault(label, "catalogue_atc")
        for row in records:
            if any(c.startswith(code) for c in row["atc_codes"]):
                members.setdefault(" / ".join(row["ingredients"]), "ema_medicines")
        for member in sorted(c for c in labels if len(c) == 7 and c.startswith(code)):
            if label := name(member):
                members.setdefault(label, "catalogue_atc")
    # The class's own name first, so a cap on added names never drops it before its members.
    ordered = {**own, **{t: members[t] for t in sorted(members, key=str.casefold) if t not in own}}
    drop = text.casefold()
    return {
        "query": query,
        "atc_codes": sorted(codes),
        "queries": {t: v for t, v in ordered.items() if t.casefold() != drop},
        "category_terms": {t: v for t, v in category.items() if t.casefold() != drop},
    }
