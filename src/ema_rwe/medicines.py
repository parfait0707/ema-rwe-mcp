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


def same_medicine(label: str, ingredients: list[str]) -> bool:
    """Whether a catalogue ATC name and an EMA ingredient list name the same medicine (salts and order aside)."""
    named = ingredient_set(label)
    have = frozenset(map(canonical, ingredients))
    return named == have or all(any(n in i or i in n for i in have) for n in named)


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
    matches = drug_expansion(text, whole_term=True)["matches"]
    if combination:
        # Never resolve a combination through one of its ingredients.
        matches = [m for m in matches if frozenset(map(canonical, m["ingredients"])) == whole]
    # Join by name: an EMA code the catalogue gives to another medicine is that record's error, not a link.
    codes |= {
        c
        for m in matches
        for c in m["atc_codes"]
        if c not in labels or same_medicine(labels[c], m["ingredients"])
    }
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
            # Only a class the catalogue itself records (its name or another member), not the drug's own code.
            if any(c.startswith(code[:5]) and c != code for c in labels):
                category |= {t: "catalogue_atc" for t in (code[:5], name(code[:5])) if t}
            continue
        if label := name(code):
            own.setdefault(label, "catalogue_atc")
        for row in records:
            under = [c for c in row["atc_codes"] if c.startswith(code)]
            # Join sources by name: skip an EMA record whose code the catalogue gives to another medicine.
            if under and all(c not in labels or same_medicine(labels[c], row["ingredients"]) for c in under):
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


# Labels that open the next field of a PASS information table; a field's value ends at the next one.
PASS_FIELDS = re.compile(
    r"(Active substance|Medicinal products?(?:\(s\))?|Product reference|Procedure number|Joint PASS|"
    r"Research question|Country\(?-?ies\)? of study|Author|Marketing authori[sz]ation holder)",
    re.IGNORECASE,
)


def known_medicine_names(labels: dict[str, str]) -> dict[str, str]:
    """Canonical medicine name -> name to record: catalogue 5th-level names and EMA INNs (single
    ingredients) as themselves, EMA product names as their ingredient set. Short and generic names are
    left out, so a match is a medicine name, not a common word."""
    names: dict[str, str] = {}
    for code, label in labels.items():
        if len(code) == 7 and not GENERIC.fullmatch(label):
            names.setdefault(canonical(label), label)
    for row in load_dictionary()[0]:
        ingredients = " / ".join(row["ingredients"])
        names.setdefault(canonical(ingredients), ingredients)
        names.setdefault(canonical(row["product_name"].split(" (")[0]), ingredients)
    return {n: v for n, v in names.items() if len(n) >= 5}


def find_medicines(text: str, names: dict[str, str]) -> list[str]:
    """Known medicine names written in the text (whole words, longest first), as names to record."""
    if not names:
        return []
    pattern = re.compile(
        r"(?<!\w)(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")(?!\w)"
    )
    return list(dict.fromkeys(names[m.group(1)] for m in pattern.finditer(canonical(text))))


def pass_table_medicines(pages, names: dict[str, str], max_pages: int = 8) -> list[dict]:
    """Medicines in the 'Active substance' and 'Medicinal product' fields of a PASS information table,
    with the ATC codes written there and the physical page. Known names only: no free text is kept."""
    for page in pages[:max_pages]:
        parts = PASS_FIELDS.split(page.text)
        found: dict[str, dict] = {}  # one entry per medicine: codes merged across its fields
        for label, value in zip(parts[1::2], parts[2::2], strict=False):
            if not label.casefold().startswith(("active substance", "medicinal product")):
                continue
            codes = set(re.findall(r"\b[A-Z]\d{2}[A-Z]{2}\d{2}\b", value))
            for name in find_medicines(value, names):
                entry = found.setdefault(
                    name, {"term": name, "atc_codes": [], "page": page.page, "source": "protocol_pass_table"}
                )
                entry["atc_codes"] = sorted(set(entry["atc_codes"]) | codes)
        if found:
            return list(found.values())
    return []
