"""Small, auditable query expansion; synonyms are not a clinical ontology."""

import re
import unicodedata

# Design, method and population vocabulary common to pharmacoepidemiology protocols, plus a few broad
# clinical words the concept dictionary has no generic entry for. Specific diseases and medicines
# belong in data/terminology.json and the EMA medicines dictionary, not here.
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
    "bleeding": ["bleeding", "haemorrhage", "hemorrhage", "haemorrhagic", "hemorrhagic", "出血"],
    "diabetes": ["diabetes", "diabetic", "糖尿病"],
    "cancer": ["cancer", "malignancy", "malignancies", "neoplasm", "oncology", "がん", "癌"],
}


def canonical(text: str) -> str:
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def contains(text: str, term: str) -> bool:
    text, term = canonical(text), canonical(term)
    if re.search(r"[^\x00-\x7f]", term):
        return term in text
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))


def expand(query: str, extra: list[str] | None = None, codes=None) -> dict:
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
    matched = {key: terms for key, terms in GROUPS.items() if any(contains(seed, t) for t in terms)}
    additions = [
        t for t in dict.fromkeys([*extra, *(t for terms in matched.values() for t in terms)]) if t.isascii()
    ]
    from .terminology import clinical_expansion

    clinical = clinical_expansion(seed, codes)
    return {
        "original_query": query,
        "expanded_query": seed + " " + " ".join(additions),
        "concepts": list(matched),
        "synonyms": additions,
        "clinical": clinical,
        "search_language": "en",
        "limitations": "Curated lexical expansion, not exhaustive equivalence. No automatic drug-class/member equivalence; ambiguous abbreviations such as AF/PS are not expanded.",
    }
