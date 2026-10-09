"""Medicine search names from the catalogue's own ATC-coded exposures and the EMA medicines dictionary.

ATC is only the join key, never a lookup answer: the catalogue writes medicines as '(B01AF02) apixaban'
or '(N03A) ANTIEPILEPTICS', while over half of its studies name medicines without any code. Every
expansion is therefore searched as names. No WHO ATC index ships; class membership comes from the
codes the catalogue and the EMA dictionary actually carry.
"""

import re

from .drugs import drug_expansion, ingredient_set, load_dictionary, without_salt
from .vocabulary import canonical

CODED = re.compile(r"^\(([A-Z]\d{2}(?:[A-Z]{1,2}(?:\d{2})?)?)\)\s*(.+)$")
# A typed class code must be 3rd/4th level: 2nd-level codes such as N03 are also ICD-10 categories.
TYPED = re.compile(r"[A-Z]\d{2}[A-Z]{1,2}(?:\d{2})?")
# ATC leaf names that mean something only under their parent code ('B05BA10 combinations').
GENERIC = re.compile(r"(?:combinations?|various|others?)", re.IGNORECASE)
MAX_NAMES = 100
# Version of the rules that record observed medicines, per source. Bump a source when its extraction
# changes what it records: a bundle built with newer rules then replaces that source's older entries.
EXPOSURE_RULES = {"catalogue_text": 2, "protocol_pass_table": 2}


def catalogue_atc(entries) -> dict[str, str]:
    """ATC code -> the catalogue's own name for it, from exposure entries such as '(N03A) ANTIEPILEPTICS'."""
    labels: dict[str, str] = {}
    for entry in entries:
        if match := CODED.match(entry.strip()):
            labels.setdefault(match[1], match[2].strip())
    return labels


def name_words(name: str) -> frozenset[str]:
    """The whole words of a medicine name without trailing salt or hydrate words, in any order."""
    return frozenset(without_salt(name).split())


def medicine_relation(label: str, ingredients: list[str]) -> str:
    """How a catalogue ATC name relates to an EMA ingredient list: 'equivalent', 'broader' (the catalogue
    name is the broader one), 'narrower' or 'different'.

    Every ingredient on each side must correspond to one on the other, so a combination never equals one of
    its ingredients ('metformin' vs empagliflozin + metformin). Two names correspond when the words of one
    contain the words of the other, salts aside and in any order: qualifiers such as '(rDNA)', 'human' or
    'type A' may differ ('insulin (human)' vs 'human insulin (rDNA)'). Words are compared whole, never by
    their letters, so a prodrug or conjugate named with a prefix stays another medicine ('aprepitant' vs
    'fosaprepitant', 'interferon' vs 'peginterferon'). Numbers identify a type, valency or strain: given by
    both they must agree ('types 16, 18' vs 'types 6, 11, 16, 18'); given by one side only, the other name
    is the broader one ('influenza, live attenuated' vs '... vaccine (H5N1) (live attenuated)')."""
    named = [name_words(n) for n in ingredient_set(label)]
    have = [name_words(i) for i in ingredients]

    def numbered(words: frozenset[str]) -> frozenset[str]:
        return frozenset(w for w in words if any(ch.isdigit() for ch in w))

    def correspond(a: frozenset[str], b: frozenset[str]) -> bool:
        return (a <= b or b <= a) and (not numbered(a) or not numbered(b) or numbered(a) == numbered(b))

    def covered(names, others) -> bool:
        return all(any(correspond(n, o) for o in others) for n in names)

    if not (covered(named, have) and covered(have, named)):
        return "different"
    label_numbers = any(numbered(n) for n in named)
    record_numbers = any(numbered(h) for h in have)
    if record_numbers and not label_numbers:
        return "broader"
    if label_numbers and not record_numbers:
        return "narrower"
    return "equivalent"


def same_medicine(label: str, ingredients: list[str]) -> bool:
    """Whether the catalogue name may carry the EMA record's code: any relation but 'different'."""
    return medicine_relation(label, ingredients) != "different"


def omitted_report(omitted: list[dict]) -> dict:
    """Every omitted class member, one per name, and their count; nothing when none. Never truncated: the
    caller decides which belong to the class, so it must see them all (54 at most in 2026)."""
    unique = list({o["name"]: o for o in omitted}.values())
    return {"omitted_members": unique, "omitted_members_total": len(unique)} if unique else {}


def expand_medicine(query: str, labels: dict[str, str]) -> dict | None:
    """Names to search for a medicine or medicine class, or None when the query is neither.

    A medicine (5th-level code) adds its catalogue name as a specific term (a broader name, see
    medicine_relation, as a category term; a narrower one, naming a strain or type the record lacks, not at
    all) and its 4th-level class as a category term. A class adds its own name, then every member name the EMA dictionary or the catalogue
    codes under it, as specific terms: a member is part of the class, not a synonym of another member.
    Combination labels stay whole, so 'metformin' never resolves to 'metformin and empagliflozin', and a
    combination ('empagliflozin and metformin', any order) resolves only to that whole ingredient set.
    A typed code counts only when some known code starts with it (no non-ATC code of the same shape).
    EMA records left out of a class because the catalogue names their code otherwise are listed in
    omitted_members, since the expansion is then not every member.
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
    # The relation decides how the catalogue name is searched: a broader name is a category term only.
    relations = {c: "equivalent" for c in codes}
    for m in matches:
        for c in m["atc_codes"]:
            relation = medicine_relation(labels[c], m["ingredients"]) if c in labels else "equivalent"
            if relation != "different" and relations.get(c) != "equivalent":
                relations[c] = relation
    codes |= set(relations)
    if not codes:
        return None

    def name(code: str) -> str | None:
        label = labels.get(code)
        return label if label and not GENERIC.fullmatch(label) else None

    own: dict[str, str] = {}
    members: dict[str, str] = {}
    category: dict[str, str] = {}
    omitted: list[dict] = []
    for code in sorted(codes):
        if len(code) == 7:
            relation = relations.get(code, "equivalent")
            if (label := name(code)) and relation == "equivalent":
                own.setdefault(label, "catalogue_atc")
            elif label and relation == "broader":
                category.setdefault(label, "catalogue_atc")  # not the query's medicine, a broader one
            # Only a class the catalogue itself records (its name or another member), not the drug's own code.
            if any(c.startswith(code[:5]) and c != code for c in labels):
                category |= {t: "catalogue_atc" for t in (code[:5], name(code[:5])) if t}
            continue
        if label := name(code):
            own.setdefault(label, "catalogue_atc")
        for row in records:
            under = [c for c in row["atc_codes"] if c.startswith(code)]
            # Join sources by name: skip an EMA record whose code the catalogue gives to another medicine, and
            # say so, since it may still belong to the class (a prodrug sharing its active form's code).
            conflicts = [c for c in under if c in labels and not same_medicine(labels[c], row["ingredients"])]
            if under and not conflicts:
                members.setdefault(" / ".join(row["ingredients"]), "ema_medicines")
            elif under:
                omitted.append(
                    {
                        "name": " / ".join(row["ingredients"]),
                        "atc_code": conflicts[0],
                        "catalogue_name": labels[conflicts[0]],
                        "reason": "the catalogue gives this code to another medicine; not added as a member",
                    }
                )
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
        **omitted_report(omitted),
    }


# Field labels of a PASS information table. A label counts only at the start of a line, and a page
# counts as the table only when it carries two or more of them: body text that merely mentions
# "medicinal products" or "active substance" is not a field.
PASS_FIELDS = re.compile(
    r"(?im)^[ \t]*(Active substances?(?:\(s\))?|Medicinal products?(?:\(s\))?|Product reference|Procedure number|"
    r"Joint PASS|Research question|Country\(?-?ies\)? of study|Author|Marketing authori[sz]ation holder)"
)
MAX_FIELD = 300  # characters of a field value: a table cell, never the rest of the page
# ATC codes of any level from the 2nd, with an ATC anatomical group letter (canonical, lower-case text)
FIELD_CODE = re.compile(r"\b([abcdghjlmnprsv]\d{2}(?:[a-z]{1,2}(?:\d{2})?)?)\b")
CODE_THEN_NAME = re.compile(r"\b[A-Z]\d{2}[A-Z]{1,2}(?:\d{2})?\s*\(\s*[A-Za-z]")
NAME_THEN_CODE = re.compile(r"\(\s*(?:ATC(?: code)?:?\s*)?[A-Z]\d{2}[A-Z]{0,2}(?:\d{2})?\s*\)")
# A code broken across a line in the PDF ("J06B A02"); never codes listed with commas ("B01A, C10")
SPLIT_CODE = re.compile(r"\b([A-Z]\d{2}[A-Z])\s+([A-Z]\d{2})\b")
_PATTERNS: dict[int, tuple[dict, re.Pattern]] = {}


def known_medicine_names(labels: dict[str, str]) -> dict[str, str]:
    """Canonical medicine name -> name to record: catalogue 5th-level names and EMA INNs as themselves,
    one-word EMA product names as their ingredient set. Multi-word product names ('COVID-19 Vaccine
    (inactivated) Valneva') read like common phrases and are left out, as are names under 5 letters."""
    names: dict[str, str] = {}
    for code, label in labels.items():
        if len(code) == 7 and not GENERIC.fullmatch(label):
            names.setdefault(canonical(label), label)
    for row in load_dictionary()[0]:
        ingredients = " / ".join(row["ingredients"])
        names.setdefault(canonical(ingredients), ingredients)
        product = canonical(row["product_name"].split(" (")[0])  # 'Spikevax (previously ...)'
        if " " not in product:
            names.setdefault(product, ingredients)
    return {n: v for n, v in names.items() if len(n) >= 5}


def known_class_names(labels: dict[str, str]) -> dict[str, str]:
    """Canonical class name -> the catalogue's own name, for 2nd-4th level codes ('(C03) DIURETICS'). Used
    only inside a PASS table's medicine fields, where a class name is what the study is about."""
    return {
        canonical(label): label
        for code, label in labels.items()
        if len(code) < 7 and not GENERIC.fullmatch(label) and len(canonical(label)) >= 5
    }


def _medicine_spans(text: str, names: dict[str, str]) -> list[tuple[str, int, int]]:
    """(name to record, start, end) of every known medicine name in canonical(text)."""
    if not names:
        return []
    cached = _PATTERNS.get(id(names))
    if not cached or cached[0] is not names:
        pattern = re.compile(
            r"(?<!\w)(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")(?!\w)"
            # 'angiotensin II receptor blockers', 'factor VIII inhibitors': a class acting on it, not it
            r"(?!\s(?:receptors?|inhibitors?|inhibition|antagonists?|agonists?|blockers?|blocking|blockade)(?!\w))"
        )
        _PATTERNS[id(names)] = cached = (names, pattern)
    return [(names[m.group(1)], m.start(), m.end()) for m in cached[1].finditer(canonical(text))]


# A study's own name ('(SONATA study)', 'SONATA is a research collaboration') and a measured substance
# ('fractional exhaled nitric oxide', 'serum ... levels') are not medicines the study is about.
ACRONYM = re.compile(
    r"\b([A-Z][A-Z0-9]{2,}(?:-[A-Z0-9]+)*)\s+(?:[Ss]tudy|[Tt]rial|[Rr]egistry|[Cc]ohort|[Pp]roject|[Pp]rogramme|[Pp]rogram|[Ss]urvey)\b"
)
MEASURED_BEFORE = re.compile(r"(?:^|\s)(?:exhaled|serum|plasma|blood|urine|urinary)\s$")
MEASURED_AFTER = re.compile(r"^\s(?:levels?|concentrations?)(?!\w)")


def find_medicines(text: str, names: dict[str, str]) -> list[str]:
    """Known medicine names written in the text (whole words), as names to record. A name that is the
    study's acronym is dropped everywhere; a name written as a measured substance is dropped there."""
    acronyms = {canonical(a) for a in ACRONYM.findall(text)}
    flat = canonical(text)
    return list(
        dict.fromkeys(
            name
            for name, start, end in _medicine_spans(text, names)
            if flat[start:end] not in acronyms
            and not MEASURED_BEFORE.search(flat[:start])
            and not MEASURED_AFTER.match(flat[end:])
        )
    )


def _field_codes(text: str, labels: dict[str, str], groups: set[str]):
    """ATC codes written in a canonical PASS field whose 2nd-level group exists ('G12C', a KRAS variant,
    does not). A 2nd-level code ('c03', also an ICD-10 category) counts only when the catalogue has codes
    under it and the field says 'ATC' or writes the code's own catalogue name."""
    for match in FIELD_CODE.finditer(text):
        code = match.group(1).upper()
        if code[:3] not in groups:
            continue
        if len(code) == 3:
            label = labels.get(code)
            named = bool(label) and canonical(label) in text
            if not (any(c.startswith(code) for c in labels) and (" atc" in f" {text}" or named)):
                continue
        yield code, match.start()


def pass_table_medicines(
    pages,
    names: dict[str, str],
    classes: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    max_pages: int = 8,
) -> list[dict]:
    """Medicines and medicine classes in the 'Active substance' and 'Medicinal product' fields of a PASS
    information table, with the ATC codes written there and the physical page. Known names (`names`;
    the catalogue's class names `classes`) and ATC codes only: no free text is kept. Each code goes to
    the name written just before it (within 60 characters), or to the field's only medicine; a code
    with no such name ('Loperamide ... ATC code: A07DA03' with loperamide unknown) is kept under the
    catalogue's name for it, or as the code itself."""
    labels = labels or {}
    groups = {c[:3] for c in labels} | {c[:3] for row in load_dictionary()[0] for c in row["atc_codes"]}
    for page in pages[:max_pages]:
        parts = PASS_FIELDS.split(page.text)
        field_labels = {" ".join(label.casefold().split())[:16] for label in parts[1::2]}
        if len(field_labels) < 2:
            continue
        found: dict[str, dict] = {}
        for label, value in zip(parts[1::2], parts[2::2], strict=False):
            if not label.casefold().startswith(("active substance", "medicinal product")):
                continue
            raw = SPLIT_CODE.sub(lambda m: m[1] + m[2], value[:MAX_FIELD])
            text = canonical(raw)
            in_class = _medicine_spans(raw, classes or {})
            # A name inside a class name ('heparin' in 'Platelet aggregation inhibitors excl. heparin') is
            # part of the class; a class written exactly where a medicine is, is that medicine.
            medicines = [
                m
                for m in _medicine_spans(raw, names)
                if not any(c[1] <= m[1] and m[2] <= c[2] and c[1:] != m[1:] for c in in_class)
            ]
            spans = medicines + [c for c in in_class if c[1:] not in {m[1:] for m in medicines}]
            for name, *_ in spans:
                found.setdefault(
                    name, {"term": name, "atc_codes": [], "page": page.page, "source": "protocol_pass_table"}
                )
            single = {name for name, *_ in medicines}
            codes = list(_field_codes(text, labels, groups))
            # 'name (code), name (code)': a code belongs to the name just before it. A field that starts
            # with 'code (name)' ('L02BB04 (enzalutamide) L02BX03 (abiraterone)') gives it to the name just
            # after. Never past a neighbouring code: an unknown medicine's code does not go to the next one.
            first = min((s[1] for s in spans), default=len(text))
            after = (
                bool(codes)
                and codes[0][1] < first
                and bool(CODE_THEN_NAME.search(raw))
                and not NAME_THEN_CODE.search(raw)
            )
            for i, (code, at) in enumerate(codes):
                prev_at = codes[i - 1][1] if i else -1
                next_at = codes[i + 1][1] if i + 1 < len(codes) else len(text)
                near = (
                    [s for s in spans if at <= s[1] < next_at and s[1] - at <= 60]
                    if after
                    else [s for s in spans if prev_at < s[1] and s[2] <= at and at - s[2] <= 60]
                )
                label = labels.get(code)
                owner = (
                    (min(near, key=lambda s: s[1]) if after else max(near, key=lambda s: s[2]))[0]
                    if near
                    # the field's only medicine and only code ('ATC code N06AX21, duloxetine')
                    else next(iter(single))
                    if len(single) == 1 and len(codes) == 1
                    else label
                    if label and not GENERIC.fullmatch(label)
                    else code
                )
                row = found.setdefault(
                    owner,
                    {"term": owner, "atc_codes": [], "page": page.page, "source": "protocol_pass_table"},
                )
                row["atc_codes"] = sorted({*row["atc_codes"], code})
        if found:
            return list(found.values())
    return []
