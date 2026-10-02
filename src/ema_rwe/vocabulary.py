"""Small, auditable query expansion; synonyms are not a clinical ontology."""

import re
import unicodedata

# Design, method and population vocabulary common to pharmacoepidemiology protocols. Clinical concepts
# (diseases, outcomes) are translated by the MCP client or an optional user dictionary, never here:
# a broad clinical word fires inside specific names (糖尿病 inside 1型糖尿病) and floods the results.
GROUPS = {
    "cohort": ["cohort", "cohorts", "コホート"],
    "case_control": ["case control", "nested case control", "症例対照"],
    "cross_sectional": ["cross sectional", "横断研究"],
    "sccs": ["SCCS", "self controlled case series", "自己対照ケースシリーズ"],
    "case_crossover": ["case crossover", "ケースクロスオーバー"],
    "new_user": ["new user", "new users", "incident user", "incident users", "新規使用者"],
    "active_comparator": ["active comparator", "active comparators", "実薬対照"],
    "drug_utilisation": ["drug utilisation", "drug utilization", "薬剤使用実態"],
    "propensity": ["propensity score", "propensity scores", "傾向スコア"],
    "weighting": ["IPTW", "inverse probability of treatment weighting", "inverse probability weighting"],
    "confounding": ["confounding", "confounder", "confounders", "交絡"],
    "immortal_time": ["immortal time", "immortal time bias", "不死時間バイアス"],
    "index_date": ["index date", "cohort entry date", "インデックス日"],
    "washout": ["washout", "wash out", "lookback", "look back", "ルックバック"],
    "followup": ["follow up", "followup", "observation period", "追跡期間"],
    "incidence": ["incidence rate", "incidence rates", "発生率", "発症率"],
    "design": ["study design", "research design", "methodology", "研究デザイン"],
    "definition": [
        "disease definition",
        "case definition",
        "outcome definition",
        "exposure definition",
        "phenotype",
        "phenotyping",
        "diagnostic criteria",
        "疾患定義",
        "アウトカム定義",
        "曝露定義",
    ],
    "validation": ["positive predictive value", "validation study", "妥当性検証"],
    "missing": [
        "missing data",
        "missing values",
        "missingness",
        "imputation",
        "欠測",
        "欠損値",
        "欠測値処理",
    ],
    "sensitivity": ["sensitivity analysis", "sensitivity analyses", "robustness analysis", "感度分析"],
    "data_source": [
        "data source",
        "data sources",
        "database",
        "databases",
        "data provenance",
        "データソース",
    ],
    "linkage": ["record linkage", "data linkage", "linked data", "データリンケージ"],
    "older": ["elderly", "older adults", "older people", "geriatric", "高齢者", "高齢"],
    "paediatric": ["paediatric", "pediatric", "children", "小児"],
}


def canonical(text: str) -> str:
    # NFKC turns '™'/'℠' into letters ('VIZAMYL™' -> 'vizamyltm'); they are marks, not part of a word.
    text = re.sub(r"[™℠]", " ", text)
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def contains(text: str, term: str) -> bool:
    text, term = canonical(text), canonical(term)
    if re.search(r"[^\x00-\x7f]", term):
        return term in text
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))


def without(text: str, terms: list[str]) -> str:
    """text with every occurrence of terms removed, longest first, matched as contains() matches."""
    text = canonical(text)
    for term in sorted({canonical(t) for t in terms}, key=len, reverse=True):
        pattern = re.escape(term)
        if not re.search(r"[^\x00-\x7f]", term):
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        text = re.sub(pattern, " ", text)
    return text


def expand(query: str, extra: list[str] | None = None, codes=None, whole_term: bool = False) -> dict:
    extra = extra or []
    if (
        not isinstance(query, str)
        or len(query) > 2000
        or not isinstance(extra, list)
        or len(extra) > 30
        or any(not isinstance(x, str) or len(x) > 150 for x in extra)
    ):
        from .domain import RWEError

        raise RWEError(
            "INVALID_INPUT", "Query <=2000 characters; at most 30 extra synonyms of <=150 characters."
        )
    seed = query + " " + " ".join(extra)
    from .terminology import clinical_expansion

    clinical = clinical_expansion(seed, codes, [query, *extra] if whole_term else None)
    # A group must not fire on text a user-dictionary concept already covers (e.g. 小児 inside a
    # dictionary name such as 小児喘息), so the concept's own terms decide what the study is about.
    residual = without(seed, clinical["matched_input_terms"])
    matched = {key: terms for key, terms in GROUPS.items() if any(contains(residual, t) for t in terms)}
    additions = [
        t for t in dict.fromkeys([*extra, *(t for terms in matched.values() for t in terms)]) if t.isascii()
    ]
    term_sources = {t: "caller" for t in extra}
    for term in additions:
        term_sources.setdefault(term, "vocabulary")
    for term, source in clinical["term_sources"].items():
        term_sources.setdefault(term, source)
    return {
        "original_query": query,
        "expanded_query": seed + " " + " ".join(additions),
        "concepts": list(matched),
        "synonyms": additions,
        "clinical": clinical,
        "term_sources": term_sources,
        "search_language": "en",
        "limitations": "Design/method vocabulary plus caller synonyms and optional user dictionaries, not exhaustive equivalence. No automatic drug-class/member equivalence; ambiguous abbreviations such as AF/PS are not expanded.",
    }
