"""Search and screening: local catalogue search, candidate counting and narrowing, search planning."""

import re

from pydantic import ValidationError

from .domain import (
    AnalogousTerm,
    CodeCandidate,
    RWEError,
    SourcePreference,
)
from .llm import (
    complete_json,
)
from .medicines import (
    GENERIC,
    MAX_NAMES,
    expand_medicine,
)
from .ranking import ScreeningBlock, order_key, screen
from .selection import SearchFilters, compact, selection
from .terminology import labelled_phrases, proposed_codes
from .unmatched import record_unmatched
from .vocabulary import expand

# ICD-10-based guidance for generating disease synonyms and analogous concepts. The MCP client (or the
# optional server LLM) follows it; the server ships no disease dictionary and no ICD-10 content.
ICD10_SYNONYM_GUIDANCE = (
    "Explore disease synonyms based on ICD-10 (WHO 2019): identify the 3-character category and the "
    "4-character subcategories the concept corresponds to, and use their titles, inclusion terms and common "
    "clinical names as English search terms, with the codes as ICD-10 code candidates (unverified, from your "
    "own knowledge). Never use Excludes terms as synonyms. Other categories in the same ICD-10 block are "
    "different diseases: give them as analogous_terms with relation=sibling; the block or chapter grouping is "
    "relation=broader; complications or related conditions are relation=associated. National modifications "
    "(ICD-10-CM, ICD-10-GM, Japanese adaptations) may differ from WHO ICD-10."
)
CATEGORY_GUIDANCE = (
    "Catalogue records often name only a category, so give each concept category_terms too: the ICD-10 "
    "block or chapter title, and the composite or umbrella outcomes studies of that concept use (for example "
    "MACE or adverse events of special interest; these are illustrations, not a list to choose from); the "
    "server adds the class of a "
    "medicine itself. Keep them "
    "specific to the concept: generic terms such as 'adverse drug reactions' only add noise. Category matches "
    "are ranked below specific ones. Codes rarely appear in catalogue records; they matter in the protocol PDFs."
)
CLIENT_EXPANSION_INSTRUCTION = (
    "Translate the question yourself and pass the result to compare_protocols as blocks, one per concept "
    "({role: outcome|condition|exposure|any, queries: 1-20 short English names and paraphrases, "
    "category_terms}); blocks are AND-ed, terms within a block OR-ed, and every column is searched with the "
    "role used for ranking. A single-concept question may instead use queries, role and category_terms. "
    + ICD10_SYNONYM_GUIDANCE
    + " "
    + CATEGORY_GUIDANCE
    + " For medicines give the English INN and the EU and US product names (each query is matched as one whole "
    "name), plus abbreviations (e.g. TNFi, JAKi, DOACs), noun variants (drugs/medicines/medications) and spellings "
    "with and without hyphens; for an absent medicine also give its 5th-level ATC code. For a requested medicine class, put the class name "
    "and its ATC 3rd/4th-level code (e.g. N03A) in queries; the server adds the member medicines that the "
    "catalogue and the EMA medicines dictionary code under it (medicine_expansion). Keep outcome, exposure "
    "and comorbidity roles apart."
)


def expansion_summary(expansion: dict) -> dict:
    """The parts of a query expansion a caller acts on; plan_study_search returns the full form."""
    clinical = expansion["clinical"]
    return {
        "synonyms": expansion["synonyms"],
        "english_terms": clinical["english_terms"],
        "related_terms": clinical["related_terms"],
        "analogous_terms": [f"{a['term']} ({a['relation']})" for a in clinical["analogous_terms"]],
        "codes": [f"{c['system']}:{c['code']}" for c in clinical["code_candidates"]],
        "drug_terms": clinical["drugs"]["english_terms"],
        "drugs_need_refresh": clinical["drugs"]["needs_refresh"],
    }


def analogous_terms(expansions: list[dict], extra: list[AnalogousTerm] | None) -> list[dict]:
    """Dictionary and caller analogous concepts with their relation, one entry per term."""
    terms = [a for e in expansions for a in e["clinical"]["analogous_terms"]]
    terms += [{**a.model_dump(), "concept_id": None} for a in extra or []]
    unique = {}
    for term in terms:
        unique.setdefault(term["term"].casefold(), term)
    return list(unique.values())


class ScreeningMixin:
    """Service methods for search and screening; uses settings, repo, client and comparisons of the Service."""

    def search_studies(
        self,
        query: str,
        limit: int = 5,
        darwin_only: bool = False,
        status: list[str] | None = None,
        analyzed_only: bool = False,
        synonyms: list[str] | None = None,
        codes: list[CodeCandidate] | None = None,
        filters: SearchFilters | None = None,
        role: str = "any",
        detail: str = "compact",
        match_scope: str = "concept",
        analogous: list[AnalogousTerm] | None = None,
    ):
        if not 1 <= limit <= 20:
            raise RWEError("INVALID_INPUT", "limit must be 1..20; preview also obeys max_comparison_studies.")
        if detail not in ("compact", "full"):
            raise RWEError("INVALID_INPUT", "detail must be compact or full.")
        candidates = self.repo.search(
            query,
            None,
            darwin_only,
            status,
            analyzed_only,
            synonyms,
            codes,
            filters,
            role,
            match_scope,
            analogous,
        )
        results = candidates[: min(limit, self.settings.max_comparison_studies)]
        for result in results:
            result["protocol_id"] = (result.get("protocol_source") or {}).get("protocol_id")
            result["local_protocols"] = [
                {k: r.get(k) for k in ("protocol_id", "local_filename", "version", "document_url")}
                for r in self.archive.list(result["study_id"])
            ]
        if detail == "compact":
            results = [
                {
                    **compact(r),
                    **{k: r[k] for k in ("protocol_id", "local_protocols", "protocol_data_sources")},
                }
                for r in results
            ]
        expansion = expand(query, synonyms, codes)
        record_unmatched(self.unmatched_log_path, query, "search_studies", expansion)
        return {
            "query": query,
            "role": role,
            **self._selection(candidates),
            **self._match_scope(
                [query],
                [expansion],
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                match_scope,
                analogous,
                bool(candidates),
                status,
                analyzed_only,
            ),
            "max_comparison_studies": self.settings.max_comparison_studies,
            "returned_count": len(results),
            "results_are_preview": len(results) < len(candidates),
            "detail": detail,
            "filters": (filters or SearchFilters()).model_dump(),
            "results": results,
            "query_expansion": expansion if detail == "full" else expansion_summary(expansion),
            "search_mode": "local_fts_near_bm25",
            "network_requests": 0,
            **self._catalogue_summary(bool(candidates)),
            "note": "Protocol fields are not_analyzed until analyze_protocol and extraction are completed. "
            "detail=full returns descriptions, provenance and the full query expansion.",
        }

    def _union(
        self,
        queries,
        darwin_only,
        synonyms,
        codes,
        filters,
        role,
        scope="concept",
        analogous=None,
        status=None,
        analyzed_only=False,
        split_long=True,
    ):
        """Deduplicated candidates of all query variants; matched_terms accumulate across variants.
        An analogous search excludes the concept matches of every variant, not just its own."""
        exclude = (
            self._concept_ids(queries, synonyms, codes, role, split_long) if scope == "analogous" else None
        )
        unique = {}
        for query in queries:
            for row in self.repo.search(
                query,
                None,
                darwin_only,
                status,
                analyzed_only,
                synonyms,
                codes,
                filters,
                role,
                scope,
                analogous,
                exclude,
                split_long,
            ):
                kept = unique.setdefault(row["study_id"], row)
                kept["matched_terms"] = list(dict.fromkeys(kept["matched_terms"] + row["matched_terms"]))
                kept["matched_term_sources"] = {**row["matched_term_sources"], **kept["matched_term_sources"]}
        return unique

    def _match_scope(
        self,
        queries,
        expansions,
        darwin_only,
        synonyms,
        codes,
        filters,
        role,
        scope,
        analogous,
        has_candidates,
        status=None,
        analyzed_only=False,
        split_long=True,
        derived=frozenset(),
    ):
        """How candidates matched, plus the analogous-concept fallback when the concept itself has no study."""
        terms = analogous_terms(expansions, analogous)
        result = {"match_scope": scope}
        if scope == "analogous":
            result["analogous_terms"] = terms
            result["analogous_note"] = (
                "Every candidate matched only an analogous concept, not the requested one; present its "
                "definitions as those of the analogous concept."
                if has_candidates
                else "No study matched these analogous concepts; relax filters or role, or propose other "
                "analogous_terms."
            )
        elif not has_candidates:
            union_args = (
                queries,
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                "analogous",
                analogous,
                status,
                analyzed_only,
                split_long,
            )
            result["analogous_fallback"] = self._fallback(
                queries, terms, synonyms, codes, role, union_args, split_long, derived
            )
        return result

    def _concept_ids(self, queries, synonyms, codes, role, split_long=True) -> set[str]:
        return set().union(*(self.repo.concept_ids(q, synonyms, codes, role, split_long) for q in queries))

    def _analogous_screen(
        self,
        queries,
        terms,
        darwin_only,
        synonyms,
        codes,
        filters,
        role,
        status=None,
        analyzed_only=False,
        split_long=True,
        derived=frozenset(),
    ) -> dict[str, dict]:
        """Analogous-concept candidates, screened and ranked like concept ones: the analogous terms form
        one block, each searched as one phrase; studies matching the requested concept are excluded.
        Each matched term reports its origin: catalogue_atc (built by the server from an ATC code),
        caller or dictionary."""
        terms = [t for t in terms if re.search(r"\w", t["term"])]  # a blank term would match every study
        if not terms:
            return {}
        # Studies that mention the requested concept anywhere are its own, not analogous candidates.
        exclude = self._concept_ids(queries, synonyms, codes, "any", split_long)

        def search(term: str, column_role: str, with_expansion: bool) -> list[dict]:
            rows = self.repo.search(
                term,
                None,
                darwin_only,
                status,
                analyzed_only,
                None,
                None,
                filters,
                column_role,
                split_long=False,
                exclude=exclude,
            )
            # The index labels a match by the term's content words ('drugs excl insulins'); report the
            # analogous term as written, so its origin and study_count line up.
            phrases = labelled_phrases(term, expand(term, whole_term=True), False)
            own = phrases[0][0] if phrases else term
            for row in rows:
                row["matched_terms"] = [term if t == own else t for t in row["matched_terms"]]
                row["matched_term_sources"] = {
                    term if t == own else t: v for t, v in row["matched_term_sources"].items()
                }
            return rows

        origin = {
            t["term"]: "catalogue_atc"
            if t["term"] in derived
            else "caller"
            if t.get("concept_id") is None
            else "dictionary"
            for t in terms
        }
        block = ScreeningBlock.model_construct(role=role, queries=list(origin), category_terms=[])
        rows = screen(search, [block])
        for row in rows:
            row["match_basis"] = "analogous"
            row["matched_term_sources"] = {
                t: origin.get(t, source) for t, source in row["matched_term_sources"].items()
            }
        return {row["study_id"]: row for row in rows}

    def _fallback(
        self, queries, terms, synonyms, codes, role, union_args, split_long=True, derived=frozenset()
    ):
        # Concept studies in the index that filters, darwin_only, status or analyzed_only removed:
        # then the concept is not absent, and the caller must say so before offering analogues.
        filtered_out = len(self._concept_ids(queries, synonyms, codes, role, split_long))
        darwin_only, filters, status, analyzed_only = (
            union_args[1],
            union_args[4],
            union_args[8],
            union_args[9],
        )
        rows = list(
            self._analogous_screen(
                queries,
                terms,
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                status,
                analyzed_only,
                split_long,
                derived,
            ).values()
        )
        selected = self._selection(rows)
        counts = {
            t["term"]: sum(t["term"].casefold() in {m.casefold() for m in r["matched_terms"]} for r in rows)
            for t in terms
        }
        if filtered_out:
            state = "concept_filtered_out"
            instruction = (
                f"{filtered_out} indexed studies match the requested concept but were removed by filters, "
                "darwin_only, status or analyzed_only. Tell the user and offer to relax those first; offer the "
                "analogous concepts below only as an alternative."
            )
        elif not terms:
            state = "no_analogous_terms"
            instruction = (
                "No study matched the requested concept and no analogous concept is known. Propose clinically "
                "analogous English concepts (broader category, sibling disease, associated condition) and rerun "
                "with match_scope='analogous' and analogous_terms. For a medicine, pass its 5th-level ATC code "
                "in queries (the server then offers its class and the class members the catalogue records) or "
                "propose medicines of the same pharmacological class. " + ICD10_SYNONYM_GUIDANCE
            )
        elif rows:
            state = "available"
            instruction = (
                "No study matched the requested concept itself. Tell the user so, show these analogous concepts "
                "with their relation and study_count, and on agreement rerun compare_protocols with "
                "match_scope='analogous' (same queries and filters, passing a block's queries and role as "
                "queries/role; phrase the question for the analogous concept). Label every result as an analogous concept, never as the requested one."
            )
        else:
            state = "no_matches"
            instruction = (
                "Neither the requested concept nor its known analogous concepts matched. Tell the user so; "
                "relax filters or role, or propose further analogous concepts via analogous_terms."
            )
        return {
            "status": state,
            "concept_index_matches_before_filters": filtered_out,
            "analogous_terms": [{**t, "study_count": counts[t["term"]]} for t in terms],
            **{k: selected[k] for k in ("total_matches", "candidates", "candidates_listed")},
            "instruction": instruction,
        }

    def _selection(self, candidates):
        return selection(candidates, self.settings.max_screening_studies, self.settings.max_listed_candidates)

    def _catalogue_summary(self, has_candidates: bool):
        """Only the catalogue facts a search caller acts on; catalogue_status has the rest."""
        catalogue = self.catalogue_status()
        return {
            "catalogue": {
                k: catalogue[k] for k in ("status", "source_type_imports", "browser_refresh_recommended")
            },
            "catalogue_action": (
                "none"
                if has_candidates
                else "browser_export_then_import"
                if catalogue["browser_refresh_recommended"]
                else "refine_queries_or_request_fresh_export"
            ),
        }

    async def compare_protocols(
        self,
        question: str,
        queries: list[str],
        filters: SearchFilters | None = None,
        darwin_only: bool = False,
        synonyms=None,
        codes=None,
        source_preference: SourcePreference | None = None,
        role: str = "any",
        study_ids: list[str] | None = None,
        match_scope: str = "concept",
        analogous: list[AnalogousTerm] | None = None,
        category_terms: list[str] | None = None,
        blocks: list[ScreeningBlock] | None = None,
        check_protocols: int = 0,
    ):
        given_blocks = bool(blocks)
        if blocks:
            if queries or synonyms or codes or category_terms or role != "any" or match_scope != "concept":
                raise RWEError(
                    "INVALID_INPUT",
                    "With blocks, put every term and role in its block (role, queries, category_terms); queries, "
                    "role, synonyms, codes, category_terms and match_scope=analogous are not combined with blocks.",
                )
            queries = [q for b in blocks for q in b.queries]
        if (
            not question.strip()
            or len(question) > 2000
            or not 1 <= len(queries) <= (40 if given_blocks else 20)
            or any(not q.strip() for q in queries)
            or not 0 <= check_protocols <= 20
        ):
            raise RWEError(
                "INVALID_INPUT",
                "A question and 1..20 nonempty search queries (1..40 across blocks) are required; "
                "check_protocols is 0..20.",
            )
        # filters.data_source_types narrows catalogue candidates; only source_preference ranks PDF evidence.
        preference = source_preference
        if not blocks:
            try:
                blocks = [ScreeningBlock(role=role, queries=queries, category_terms=category_terms or [])]
            except ValidationError as exc:
                raise RWEError(
                    "INVALID_INPUT", "Invalid role or category_terms (1..20 nonempty terms)."
                ) from exc
        # The single block's role drives the analogous fallback, whichever form the caller used.
        role = blocks[0].role if len(blocks) == 1 else role
        medicines: list[dict] = []
        derived: set[str] = set()  # analogous terms the server built from ATC codes
        if match_scope == "analogous":
            # The ATC class offered on a zero-hit result is searched on rerun, as the fallback said.
            atc_terms = self._atc_analogues(queries)
            derived |= {t.term for t in atc_terms}
            analogous = [*(analogous or []), *atc_terms]
            terms = analogous_terms([expand(q, synonyms, codes, whole_term=True) for q in queries], analogous)
            unique = self._analogous_screen(
                queries, terms, darwin_only, synonyms, codes, filters, role, split_long=False, derived=derived
            )
        else:

            def search(term: str, column_role: str, with_expansion: bool) -> list[dict]:
                return self.repo.search(
                    term,
                    None,
                    darwin_only,
                    None,
                    False,
                    synonyms if with_expansion else None,
                    codes if with_expansion else None,
                    filters,
                    column_role,
                    split_long=False,
                )

            # Every column, ranked: the role is a ranking signal, not a filter (empty Outcomes fields).
            sources: dict[str, str] = {}
            unique = {
                row["study_id"]: row
                for row in screen(search, self._with_medicines(blocks, medicines, sources))
            }
            if not unique and len(blocks) == 1:
                # An absent medicine: offer the catalogue's members of the class its ATC code belongs to.
                atc_terms = self._atc_analogues(blocks[0].queries)
                derived |= {t.term for t in atc_terms}
                analogous = [*(analogous or []), *atc_terms]
            for row in unique.values():
                # Server-added medicine names say where they came from, not that the caller typed them.
                for term, source in row["matched_term_sources"].items():
                    if source == "query" and term in sources:
                        row["matched_term_sources"][term] = sources[term]
        candidates = list(unique.values())
        checked = 0
        listed = self.settings.max_screening_studies < len(candidates) <= self.settings.max_listed_candidates
        requests = 0
        if check_protocols and listed and study_ids is None and match_scope == "concept":
            before = getattr(self.client, "requests_made", 0)
            checked = await self._check_protocols(candidates[:check_protocols])
            requests = getattr(self.client, "requests_made", 0) - before
            candidates.sort(key=order_key)
        # Reported as matched: each variant is one whole name, as in the search itself.
        expansions = [expand(q, synonyms, codes, whole_term=True) for q in queries]
        for query, expansion in zip(queries, expansions):
            record_unmatched(self.unmatched_log_path, query, "compare_protocols", expansion)
        if study_ids is not None:
            # An explicit user choice among the listed candidates; never a silent top N.
            if (
                not study_ids
                or len(study_ids) != len(set(study_ids))
                or len(study_ids) > self.settings.max_screening_studies
                or not set(study_ids) <= set(unique)
            ):
                raise RWEError(
                    "INVALID_INPUT",
                    "study_ids must be unique candidate Study IDs from this search, within max_screening_studies.",
                )
            candidates = [unique[sid] for sid in study_ids]
        search = {
            **self._selection(candidates),
            "max_comparison_studies": self.settings.max_comparison_studies,
            "source_preference": preference.model_dump() if preference else None,
            "queries": queries,
            "role": role,
            "blocks": [b.model_dump() for b in blocks],
            "medicine_expansion": medicines,
            "ranking": "Candidates are ordered, never cut: specific-term matches before category-only matches, "
            "matches in the role's own columns first, studies whose export lists a protocol before unlisted ones, "
            "secondary-use data before surveys, then fused BM25 rank; studies with an image-only protocol go last"
            + ("; studies checked to have no published protocol go last." if checked else "."),
            "protocols_checked": checked,
            "selected_study_ids": study_ids,
            "filters": (filters or SearchFilters()).model_dump(),
            "query_expansions": [expansion_summary(e) for e in expansions],
            **self._match_scope(
                queries,
                expansions,
                darwin_only,
                synonyms,
                codes,
                filters,
                role,
                match_scope,
                analogous,
                bool(unique) or len(blocks) > 1,
                split_long=False,
                derived=frozenset(derived),
            ),
            "darwin_only": darwin_only,
            "search_scope": "local catalogue metadata and saved analysis only",
            **self._catalogue_summary(bool(candidates)),
        }
        if len(blocks) > 1 and not unique:
            search["analogous_fallback"] = {
                "status": "not_available_for_blocks",
                "instruction": "No study matched every block. Report each block separately (run compare_protocols "
                "per block) or broaden a block with category_terms; analogous concepts apply to one block at a time.",
            }
        if not 1 <= len(candidates) <= self.settings.max_screening_studies:
            return {**search, "network_requests": requests, "rows": [], "pdf_downloads": 0}
        return await self.comparisons.prepare(question, candidates, search)

    def _with_medicines(
        self, blocks: list[ScreeningBlock], report: list[dict], sources: dict[str, str]
    ) -> list[ScreeningBlock]:
        """Blocks with the names of each medicine or medicine class added (see medicines.expand_medicine).

        At most MAX_NAMES specific names are added per question, the class names first. `report` records
        what was added per query; `sources` maps each added name the caller typed in no block to its origin.
        """
        labels = self.repo.atc_labels()
        typed = {t.casefold() for b in blocks for t in [*b.queries, *b.category_terms]}
        budget = MAX_NAMES
        out = []
        for index, block in enumerate(blocks):
            known = {t.casefold() for t in [*block.queries, *block.category_terms]}
            queries, category = list(block.queries), list(block.category_terms)
            for query in block.queries:
                found = expand_medicine(query, labels)
                if not found:
                    continue
                names = [t for t in found["queries"] if t.casefold() not in known]
                added = {"queries": names[:budget], "omitted": len(names[budget:])}
                budget -= len(added["queries"])
                added["category_terms"] = [t for t in found["category_terms"] if t.casefold() not in known]
                known |= {t.casefold() for t in [*added["queries"], *added["category_terms"]]}
                queries += added["queries"]
                category += added["category_terms"]
                sources |= {t: found["queries"][t] for t in added["queries"] if t.casefold() not in typed}
                report.append({"block": index, **found, **added})
            out.append(block.model_copy(update={"queries": queries, "category_terms": category}))
        return out

    def _atc_analogues(self, queries: list[str]) -> list[AnalogousTerm]:
        """Analogous concepts for a medicine with no study: its 4th-level ATC class (broader) and the class
        members the catalogue records (sibling), from the 5th-level codes the caller typed. Names only."""
        labels = self.repo.atc_labels()
        terms = []
        for query in queries:
            code = query.strip().upper()
            if not re.fullmatch(r"[A-Z]\d{2}[A-Z]{2}\d{2}", code):
                continue
            parent = code[:5]
            members = [
                labels[c] for c in sorted(labels) if len(c) == 7 and c.startswith(parent) and c != code
            ]
            broader = labels.get(parent) or labels.get(code[:4])  # the 3rd level when the 4th has no name
            for name, relation in [(broader, "broader"), *((m, "sibling") for m in members)]:
                if name and name.isascii() and len(name) <= 150 and not GENERIC.fullmatch(name):
                    terms.append(AnalogousTerm(term=name, relation=relation))
        return terms

    async def _check_protocols(self, rows: list[dict]) -> int:
        """Mark whether each top candidate's Study documents list a protocol (no PDF download). A study
        without one cannot answer a definition question; it is ranked last, never removed."""
        for row in rows:
            try:
                await self.get_protocol(row["study_id"], download=False)
                row["protocol_available"] = row["protocol_found"] = True
            except RWEError as exc:
                # No protocol, or no longer a non-interventional study: neither can answer, so rank last.
                unusable = exc.code in ("PROTOCOL_NOT_FOUND", "STUDY_OUT_OF_SCOPE")
                row["protocol_available"] = False if unusable else None
                if exc.code == "PROTOCOL_NOT_FOUND":
                    row["protocol_found"] = False
                if exc.code == "STUDY_OUT_OF_SCOPE":
                    row["out_of_scope"] = True
        return len(rows)

    async def plan_study_search(self, question: str, use_llm: bool = False):
        expansion = expand(question)
        clinical = expansion["clinical"]
        logged = record_unmatched(self.unmatched_log_path, question, "plan_study_search", expansion)
        queries = list(
            dict.fromkeys(
                clinical["english_terms"]
                + clinical["related_terms"]
                + [
                    v
                    for c in clinical["code_candidates"]
                    if c["origin"] != "official_dictionary"  # an EMA record's code is no catalogue query
                    for v in c["search_variants"]
                ]
            )
        )
        if not queries:
            queries = expansion["synonyms"] or ([question] if question.isascii() else [])
        # Design words (cohort, 傾向スコア) translate locally, but the clinical concept of a Japanese
        # question is known only when a dictionary concept matched.
        untranslated = not question.isascii() and not clinical["concepts"]
        plan = {
            "question": question,
            "query_expansion": expansion,
            "queries": queries,
            "target_role": "outcome"
            if "アウトカム" in question or "outcome" in question.lower()
            else "unspecified",
            "status": "needs_client_translation" if untranslated or not queries else "planned",
            "unmatched_logged": logged,
            "dictionaries": clinical["dictionaries"],
            "client_expansion": {
                "required": untranslated or not queries,
                "instruction": CLIENT_EXPANSION_INSTRUCTION,
            },
            "code_systems_to_check": clinical["systems_to_check"],
            "analogous_terms": clinical["analogous_terms"],
            "instruction": "Search English names, related names and codes independently. Then inspect outcome/exposure definitions, "
            "code lists and appendices in the selected PDFs. Check the source database, vocabulary/version, "
            "code-set membership and algorithm (counts, time windows, exclusions). A code hit alone does not prove outcome use.",
            "method": "user_dictionary" if clinical["concepts"] else "client_expansion",
            "note": "Review synonyms for ambiguity; search multiple formulations and compare results.",
        }
        if use_llm:
            result = await complete_json(
                self.settings,
                [
                    {
                        "role": "system",
                        "content": "Plan EMA RWD protocol searches. Translate the user's clinical concepts into English. "
                        "Return JSON {queries: [up to 5 English keyword/code queries], synonyms: [up to 30 English search phrases], "
                        "code_candidates: [{system, code, label, vocabulary_version, relation, source_url}], "
                        "analogous_terms: [{term, relation}], uncertainties: [strings]}. "
                        "For code_candidates, relation is candidate/related/broader/narrower/unspecified_subtype; version, label "
                        "and URL may be null. analogous_terms are up to 10 clinically analogous but different English concepts, "
                        "searched only when the requested one has no study; their relation is only broader (category), sibling "
                        "(another disease of the same category) or associated (complication or related condition). "
                        "Consider ICD-10 and national modifications, ICD-9-CM, SNOMED CT, Read/CTV3, MedDRA, OMOP, ATC, RxNorm, "
                        "NDC, LOINC or local codes according to source data. Do not invent codes/URLs: omit uncertain codes and "
                        "describe required master lookup. Suggestions are unverified retrieval candidates, not study definitions. "
                        "Include separate name-based and code-based queries. Distinguish outcome vs exposure vs comorbidity. "
                        + ICD10_SYNONYM_GUIDANCE
                        + " "
                        "For medicines, translate non-English names, expand brand names to International Nonproprietary Names "
                        "and INNs to brand names in BOTH directions, and suggest ATC codes when known. Put translated "
                        "drug names in synonyms so the local official EMA dictionary can verify and expand them. "
                        "Preserve full ingredient sets for combination products, salts, formulations and country differences. "
                        "Do not equate a broad disease with an unspecified subtype or replace a class with one drug. No completeness claim.",
                    },
                    {"role": "user", "content": question},
                ],
            )
            queries = result.get("queries")
            if (
                not isinstance(queries, list)
                or not 1 <= len(queries) <= 5
                or any(
                    not isinstance(q, str) or not q.strip() or len(q) > 500 or not q.isascii()
                    for q in queries
                )
            ):
                raise RWEError(
                    "SCHEMA_VALIDATION_FAILED", "Search planner must return 1..5 bounded query strings."
                )
            extra = result.get("synonyms", [])
            if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
                raise RWEError("SCHEMA_VALIDATION_FAILED", "Search planner synonyms must be strings.")
            plan.update(
                method="llm_query_plan",
                queries=queries,
                query_expansion=expand(question, extra, proposed_codes(result.get("code_candidates", []))),
                status="planned",
                uncertainties=result.get("uncertainties", []),
            )
            plan["client_expansion"]["required"] = False  # the server LLM already translated
            sources = plan["query_expansion"]["term_sources"]
            for term in extra:
                if sources.get(term) == "caller":
                    sources[term] = "llm"
            try:
                proposed = [AnalogousTerm.model_validate(a) for a in result.get("analogous_terms") or []][:10]
            except (ValidationError, TypeError) as exc:
                raise RWEError(
                    "SCHEMA_VALIDATION_FAILED", "Search planner analogous_terms are invalid."
                ) from exc
            plan["analogous_terms"] = analogous_terms([plan["query_expansion"]], proposed)
        plan["queries"] = list(
            dict.fromkeys(
                [
                    *plan["queries"],
                    *plan["query_expansion"]["clinical"]["english_terms"],
                    *plan["query_expansion"]["clinical"]["related_terms"],
                ]
            )
        )
        plan["code_searches"] = [
            {"query": c["code"], "codes": [{k: v for k, v in c.items() if k != "search_variants"}]}
            for c in plan["query_expansion"]["clinical"]["code_candidates"]
            if c["origin"] != "official_dictionary"  # catalogue searches never key on an EMA record's code
        ]
        return plan
