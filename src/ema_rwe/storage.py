import csv
import hashlib
import io
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .domain import RWEError, Study, now
from .ema import BASE, is_non_interventional, norm
from .medicines import catalogue_atc
from .selection import filter_rows
from .terminology import analogous_phrases, labelled_phrases, term_source
from .vocabulary import canonical, expand

SCHEMA_VERSION = 5  # 5: Study.protocol_listed / protocol_text_layer
FTS_COLUMNS = (
    "id UNINDEXED",
    "title",
    "metadata",
    "design",
    "definitions",
    "data_sources",
    "condition",
    "outcomes",
    "exposure",
    "objective",
)
# bm25 weights per FTS column (id has none). Title and the role-specific columns outrank free text.
BM25_WEIGHTS = (0, 3, 1, 5, 4, 3, 4, 4, 4, 2)
# Which columns a role-scoped search may match. Title is always allowed: it names the outcome.
ROLE_COLUMNS = {
    "any": None,
    "outcome": ("title", "outcomes", "objective", "definitions"),
    "condition": ("title", "condition"),
    "exposure": ("title", "exposure", "data_sources"),
}


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RWEError("DATABASE_ERROR", "Database schema is newer than this server supports.")
            if version == SCHEMA_VERSION:
                return  # Committed catalogue: a no-op DDL still bumps the header and dirties git.
            db.executescript(f"""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS studies (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                DROP TABLE IF EXISTS study_fts;
                CREATE VIRTUAL TABLE study_fts USING fts5(
                    {", ".join(FTS_COLUMNS)},
                    tokenize='unicode61 remove_diacritics 2');
                CREATE TABLE IF NOT EXISTS analyses (
                    study_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS analysis_history (
                    snapshot_id TEXT PRIMARY KEY, study_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports (
                    checksum TEXT PRIMARY KEY, filename TEXT, imported_at TEXT, count INTEGER);
                CREATE TABLE IF NOT EXISTS study_catalogue_search (
                    study_id TEXT PRIMARY KEY, search_text TEXT NOT NULL);
                PRAGMA user_version={SCHEMA_VERSION};
            """)
            # Re-index existing rows; role columns stay empty until the export is imported again.
            for row in db.execute("SELECT body FROM studies").fetchall():
                self._index(db, Study.model_validate_json(row[0]))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        except sqlite3.Error as exc:
            raise RWEError("DATABASE_ERROR", "SQLite operation failed.") from exc
        finally:
            db.close()

    def get(self, study_id: str) -> Study | None:
        with self.connection() as db:
            row = db.execute("SELECT body FROM studies WHERE id=?", (study_id,)).fetchone()
        return Study.model_validate_json(row[0]) if row else None

    def study_count(self) -> int:
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM studies").fetchone()[0]

    def imports(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT checksum, filename, imported_at, count FROM imports ORDER BY imported_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert(self, study: Study):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO studies VALUES (?,?)", (study.study_id, study.model_dump_json())
            )
            self._index(db, study)

    def _index(self, db, study):
        row = db.execute("SELECT body FROM analyses WHERE study_id=?", (study.study_id,)).fetchone()
        a = json.loads(row[0])["analysis"] if row else {}
        catalogue_row = db.execute(
            "SELECT search_text FROM study_catalogue_search WHERE study_id=?", (study.study_id,)
        ).fetchone()
        catalogue_search_text = catalogue_row[0] if catalogue_row else ""
        db.execute("DELETE FROM study_fts WHERE id=?", (study.study_id,))

        # Only structured facts are indexed; never protocol raw text or contact fields.
        def values(value):
            if isinstance(value, list):
                return " ".join(values(x) for x in value)
            return value.get("value", "") if isinstance(value, dict) else ""

        db.execute(
            "INSERT INTO study_fts VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                study.study_id,
                study.title,
                " ".join(
                    [
                        study.description,
                        study.study_type,
                        study.status,
                        *study.countries,
                        *study.data_source_types,
                        catalogue_search_text,
                        values(a.get("population")),
                        values(a.get("comparator")),
                    ]
                ),
                " ".join(study.study_designs)
                + " "
                + values(a.get("study_design"))
                + " "
                + values(a.get("statistical_analysis")),
                values(a.get("disease_definitions")),
                values(a.get("data_sources"))
                + " "
                + " ".join(
                    " ".join([s["value"], *s["types"], s["role"], s["definition"]])
                    for s in a.get("source_assessments", [])
                ),
                " ".join(study.conditions),
                study.outcomes + " " + values(a.get("outcomes")),
                " ".join(study.exposures) + " " + values(a.get("exposure")),
                study.objective,
            ),
        )

    def analysis(self, study_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT body FROM analyses WHERE study_id=?", (study_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_analysis(self, study: Study, result: dict):
        with self.connection() as db:
            self._archive_analysis(db, study.study_id, replacement=result)
            db.execute(
                "INSERT OR REPLACE INTO analyses VALUES (?,?,?)",
                (study.study_id, result["source"]["fingerprint"], json.dumps(result, ensure_ascii=False)),
            )
            self._index(db, study)

    def invalidate_analysis(self, study: Study):
        with self.connection() as db:
            self._archive_analysis(db, study.study_id)
            db.execute("DELETE FROM analyses WHERE study_id=?", (study.study_id,))
            self._index(db, study)

    @staticmethod
    def _archive_analysis(db, study_id, replacement=None):
        row = db.execute("SELECT fingerprint, body FROM analyses WHERE study_id=?", (study_id,)).fetchone()
        if row:
            if (
                replacement
                and row["fingerprint"] == replacement["source"]["fingerprint"]
                and json.loads(row["body"])["analysis"] == replacement["analysis"]
            ):
                return  # Updating freshness timestamps alone is not a new extraction revision.
            db.execute(
                "INSERT OR IGNORE INTO analysis_history VALUES (?,?,?,?)",
                (hashlib.sha256(row["body"].encode()).hexdigest(), study_id, row["fingerprint"], row["body"]),
            )

    def atc_labels(self) -> dict[str, str]:
        """ATC code -> the catalogue's own name, from exposures such as '(B01AF) Direct factor Xa inhibitors'."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT DISTINCT j.value FROM studies, json_each(studies.body, '$.exposures') j WHERE j.value LIKE '(%'"
            ).fetchall()
        return catalogue_atc(r[0] for r in rows)

    def concept_ids(
        self, query: str, synonyms=None, codes=None, role: str = "any", split_long: bool = True
    ) -> set[str]:
        """Index matches of the requested concept before any filter: the analogous-scope exclusion and
        the evidence that a zero-hit result came from filters rather than from the catalogue."""
        groups = [
            group
            for _, group in labelled_phrases(
                query, expand(query, synonyms, codes, whole_term=not split_long), split_long
            )
        ]
        match = fts_match(groups, ROLE_COLUMNS[role])
        if not match:
            return set()
        with self.connection() as db:
            return {r[0] for r in db.execute("SELECT id FROM study_fts WHERE study_fts MATCH ?", (match,))}

    def search(
        self,
        query: str,
        limit: int | None,
        darwin_only: bool,
        status: list[str] | None,
        analyzed_only: bool = False,
        synonyms: list[str] | None = None,
        codes=None,
        filters=None,
        role: str = "any",
        scope: str = "concept",
        analogous=None,
        exclude: set[str] | None = None,
        split_long: bool = True,
    ) -> list[dict]:
        """scope=concept searches the query and its expansion; scope=analogous searches only analogous
        concepts (dictionary + caller terms) and drops studies the concept search would also match,
        plus exclude (the concept matches of sibling query variants)."""
        if (limit is not None and not 1 <= limit <= 20) or len(query) > 2000:
            raise RWEError("INVALID_INPUT", "limit must be 1..20; query must be at most 2000 characters.")
        if role not in ROLE_COLUMNS:
            raise RWEError("INVALID_INPUT", "role must be one of any, outcome, condition, exposure.")
        if scope not in ("concept", "analogous"):
            raise RWEError("INVALID_INPUT", "scope must be concept or analogous.")
        # A single-phrase query (compare_protocols) is one medicine name for dictionary expansion: a name
        # inside a longer one ('glucagon' in 'glucagon-like peptide-1 receptor agonists') is not expanded.
        expansion = expand(query, synonyms, codes, whole_term=not split_long)
        columns = ROLE_COLUMNS[role]
        phrases = (
            labelled_phrases(query, expansion, split_long)
            if scope == "concept"
            else analogous_phrases(expansion, analogous)
        )
        match = fts_match([group for _, group in phrases], columns)
        with self.connection() as db:
            if (query.strip() or scope == "analogous") and not match:
                return []
            # Which expansion terms each study matched, so a result shows why it is a candidate.
            matched_terms = {}
            for label, group in phrases if match else []:
                for (study_id,) in db.execute(
                    "SELECT id FROM study_fts WHERE study_fts MATCH ?", (fts_match([group], columns),)
                ):
                    terms = matched_terms.setdefault(study_id, [])
                    if label not in terms:
                        terms.append(label)
        excluded = set(exclude or ())
        if scope == "analogous":
            excluded |= self.concept_ids(query, synonyms, codes, role, split_long)
        with self.connection() as db:
            if match:
                rows = db.execute(
                    f"""SELECT s.body, bm25(study_fts,{",".join(map(str, BM25_WEIGHTS))}) AS rank
                    FROM study_fts JOIN studies s ON s.id=study_fts.id
                    WHERE study_fts MATCH ? ORDER BY rank, s.id""",
                    (match,),
                ).fetchall()
            else:
                rows = db.execute("SELECT body, 0 AS rank FROM studies ORDER BY id").fetchall()
        results = []
        for row in rows:
            study = Study.model_validate_json(row["body"])
            if study.study_id in excluded:
                continue
            if not is_non_interventional(study.study_type):
                continue
            if darwin_only and study.darwin_eu is not True:
                continue
            if status and study.status.casefold() not in {s.casefold() for s in status}:
                continue
            analysis = self.analysis(study.study_id)
            if analyzed_only and not analysis:
                continue
            result = study.model_dump(exclude={"tabs"})
            if not result["study_designs"] and analysis and analysis["analysis"].get("study_design"):
                result["study_designs"] = [analysis["analysis"]["study_design"]["value"]]
            if not filter_rows([result], filters):
                continue
            result.update(
                score=-row["rank"],
                protocol_data_sources=(analysis["analysis"]["data_sources"] if analysis else []),
                protocol_source_assessments=(
                    analysis["analysis"].get("source_assessments", []) if analysis else []
                ),
                protocol_data_sources_status="extracted" if analysis else "not_analyzed",
                data_source_types_status="available" if study.data_source_types else "not_provided",
                protocol_source=analysis["source"] if analysis else None,
                analysis_available=bool(analysis),
                match_basis=scope,
                matched_terms=matched_terms.get(study.study_id, []),
                matched_term_sources={
                    t: term_source(t, expansion, analogous) for t in matched_terms.get(study.study_id, [])
                },
            )
            results.append(result)
            if limit is not None and len(results) >= limit:
                break
        return results


def fts_match(groups: list, columns: tuple[str, ...] | None) -> str:
    """OR of word groups. A group is a word list, or (words, distance) when its words were picked out
    of longer text. Multi-word groups must co-occur in one column: NEAR distance (tokens between the
    first and last word) is max(3, words - 1), or the given distance when larger."""
    clauses = []
    for group in groups:
        group, distance = group if isinstance(group, tuple) else (group, 0)
        words = ['"' + canonical(w).replace('"', '""') + '"' for w in group if canonical(w)]
        if not words:
            continue
        # A fixed distance would never match an adjacent phrase of six or more words.
        near = max(3, len(words) - 1, distance)
        clauses.append(words[0] if len(words) == 1 else f"NEAR({' '.join(words)}, {near})")
    if not clauses:
        return ""
    expr = " OR ".join(clauses)
    return f"{{{' '.join(columns)}}}: ({expr})" if columns else expr


ALIASES = {
    "study_id": ("study id", "study identifier", "id"),
    "title": ("official title and acronym", "study title", "title"),
    "study_type": ("study type", "type of study"),
    "study_designs": ("non interventional study design", "study design", "study designs"),
    "description": ("study description", "description"),
    "eupas_number": ("eu pas number", "eupas number"),
    "status": ("study status", "status"),
    "darwin_eu": ("darwin eu study", "darwin eu"),
    "countries": ("study countries", "countries"),
    "data_source_types": ("data sources types", "data source type", "data source types"),
    "catalogue_data_sources": (
        "data sources",
        "data source s",
        "data source name",
        "data source names",
    ),
    "conditions": ("medicinal condition to be studied", "medical condition", "conditions"),
    "outcomes": ("outcomes", "outcome"),
    "exposures": (
        "study drug international non proprietary name inn or common name",
        "medicinal product name",
        "inn",
    ),
    "objective": ("main study objective", "objective"),
}
# Secondary columns appended to the role fields when present.
ROLE_EXTRA_COLUMNS = {
    "conditions": ("additional medical condition s",),
    "exposures": (
        "medicinal product name",
        "medicinal product name other",
        "anatomical therapeutic chemical atc code",
    ),
}

# Public clinical metadata that improves candidate retrieval without indexing contact fields.
SEARCH_COLUMNS = (
    "study topic",
    "scope of the study",
    "data collection methods",
    "study design",
    "main study objective",
    "non interventional study design other",
    "medicinal product name",
    "medicinal product name other",
    "study drug international non proprietary name inn or common name",
    "anatomical therapeutic chemical atc code",
    "medicinal condition to be studied",
    "additional medical condition s",
    "short description of the study population",
    "setting",
    "comparators",
    "outcomes",
    "data analysis plan",
    "other linked data sources",
)


def split_values(text: str) -> list[str]:
    """Catalogue multi-value cells separate items with |, ; or line breaks."""
    return [x.strip() for x in re.split(r"[|;\n]+", text or "") if x.strip()]


def import_csv(
    repo: Repository, path: Path, column_map: dict[str, str] | None = None, source_type: str | None = None
) -> dict:
    """Upsert one official Studies export. With source_type, every record is tagged with that catalogue type.

    Filtered exports (claims/ehr/registry) share the plain export schema; only the file name carries the type.
    Tags accumulate across typed imports and survive later untyped imports of the full export.
    """
    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RWEError("CSV_SCHEMA_ERROR", "Export CSV must be UTF-8 (BOM supported).") from exc
    try:
        delimiter = csv.Sniffer().sniff(text[:16384], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    # EMA values contain RFC 4180 doubled quotes. Sniffer can incorrectly infer
    # doublequote=False from a sample and shift all subsequent columns.
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, quotechar='"', doublequote=True)
    fieldnames = reader.fieldnames or []
    headers = {norm(h): h for h in fieldnames}
    selected = {
        key: next((headers[a] for a in aliases if a in headers), None) for key, aliases in ALIASES.items()
    }
    selected.update(column_map or {})
    if any(key not in ALIASES or value not in fieldnames for key, value in (column_map or {}).items()):
        raise RWEError("CSV_SCHEMA_ERROR", "column-map must map known internal fields to existing headers.")
    role_columns = {
        key: [c for c in [selected.get(key), *(headers.get(h) for h in ROLE_EXTRA_COLUMNS.get(key, ()))] if c]
        for key in ("conditions", "outcomes", "exposures", "objective")
    }
    if any(
        not selected.get(key) or selected[key] not in fieldnames
        for key in ("study_id", "title", "study_type")
    ):
        raise RWEError(
            "CSV_SCHEMA_ERROR",
            "Required CSV columns: Study ID, Official title and acronym, "
            "Study type. Use --column-map for alternate headers. Found: " + str(fieldnames),
        )
    protocol_columns = [
        headers[h] for h in ("protocol file s", "protocol file s uri", "protocol url") if h in headers
    ]
    other_sources_column = headers.get("other linked data sources")
    other_design_column = headers.get("non interventional study design other")
    search_columns = [headers[key] for key in SEARCH_COLUMNS if key in headers]
    duplicate_headers = sorted({header for header in fieldnames if fieldnames.count(header) > 1})
    studies, skipped, seen, excluded_ids = [], 0, set(), []
    search_text_by_id = {}
    for lineno, row in enumerate(reader, 2):
        v = {key: (row.get(col) or "").strip() for key, col in selected.items()}
        sid = v["study_id"]
        if not re.fullmatch(r"\d{1,20}", sid) or sid in seen:
            raise RWEError("CSV_SCHEMA_ERROR", f"Invalid or duplicate Study ID at CSV record {lineno}.")
        seen.add(sid)
        if not is_non_interventional(v["study_type"]):
            skipped += 1
            excluded_ids.append(sid)
            continue
        if not v["title"]:
            raise RWEError("CSV_SCHEMA_ERROR", f"Invalid or duplicate Study ID/title at CSV record {lineno}.")
        for key in ("countries", "data_source_types", "catalogue_data_sources", "study_designs"):
            v[key] = split_values(v.get(key, ""))
        for key, columns in role_columns.items():
            texts = [(row.get(c) or "").strip() for c in columns]
            if key in ("conditions", "exposures"):
                v[key] = list(dict.fromkeys(x for t in texts for x in split_values(t)))
            else:
                v[key] = "\n".join(t for t in texts if t)
        if other_sources_column:
            v["catalogue_data_sources"] += split_values(row.get(other_sources_column))
            v["catalogue_data_sources"] = list(dict.fromkeys(v["catalogue_data_sources"]))
        if other_design_column:
            v["study_designs"] += split_values(row.get(other_design_column))
            v["study_designs"] = list(dict.fromkeys(v["study_designs"]))
        v["darwin_eu"] = {"yes": True, "true": True, "1": True, "no": False, "false": False, "0": False}.get(
            v.get("darwin_eu", "").lower()
        )
        v["eupas_number"] = v.get("eupas_number") or None
        if protocol_columns:
            v["protocol_listed"] = any((row.get(c) or "").strip() for c in protocol_columns)
        studies.append(Study(**v, source_url=f"{BASE}/study/{sid}", metadata_source=f"CSV SHA256:{checksum}"))
        search_text_by_id[sid] = "\n".join(
            dict.fromkeys((row.get(column) or "").strip() for column in search_columns)
        ).strip()
    # Preserve original bytes outside the search index. The raw official export may contain contact data.
    archive = repo.path.parent / "raw" / f"{checksum}.csv"
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        archive.write_bytes(raw)
    with repo.connection() as db:
        for sid in excluded_ids:
            repo._archive_analysis(db, sid)
            db.execute("DELETE FROM study_fts WHERE id=?", (sid,))
            db.execute("DELETE FROM analyses WHERE study_id=?", (sid,))
            db.execute("DELETE FROM study_catalogue_search WHERE study_id=?", (sid,))
            db.execute("DELETE FROM studies WHERE id=?", (sid,))
        for study in studies:
            old_row = db.execute("SELECT body FROM studies WHERE id=?", (study.study_id,)).fetchone()
            old = Study.model_validate_json(old_row[0]) if old_row else None
            if source_type:
                study.data_source_types = list(
                    dict.fromkeys([*(old.data_source_types if old else []), source_type])
                )
                study.data_source_types_source = f"filtered export {path.name} SHA256:{checksum}"
                study.data_source_types_checked_at = study.retrieved_at
            elif not selected.get("data_source_types") and old:
                study.data_source_types = old.data_source_types
                study.data_source_types_source = old.data_source_types_source or old.metadata_source
                study.data_source_types_checked_at = (
                    old.data_source_types_checked_at or old.detail_checked_at or old.retrieved_at
                )
            elif selected.get("data_source_types"):
                study.data_source_types_source = study.metadata_source
                study.data_source_types_checked_at = study.retrieved_at
            db.execute(
                "INSERT OR REPLACE INTO studies VALUES (?,?)", (study.study_id, study.model_dump_json())
            )
            db.execute(
                "INSERT OR REPLACE INTO study_catalogue_search VALUES (?,?)",
                (study.study_id, search_text_by_id[study.study_id]),
            )
            repo._index(db, study)
        db.execute(
            "INSERT OR REPLACE INTO imports VALUES (?,?,?,?)", (checksum, path.name, now(), len(studies))
        )
    schema_warnings = []
    if not selected.get("data_source_types") and not source_type:
        schema_warnings.append(
            "CSV has no Data source type column; import filtered exports from source_type/ to tag studies."
        )
    if duplicate_headers:
        schema_warnings.append("Duplicate non-indexed CSV headers: " + ", ".join(duplicate_headers))
    return {
        "imported": len(studies),
        "source_type": source_type,
        "skipped_out_of_scope": skipped,
        "checksum": checksum,
        "mode": "upsert; records absent from this export are retained",
        "schema_warnings": schema_warnings,
    }


# Catalogue tables a newer bundled database replaces; everything else (cached analyses, answers,
# history) is the user's own work and is never touched.
CATALOGUE_TABLES = ("studies", "imports", "study_catalogue_search", "study_fts")


def latest_import(path: Path, immutable: bool = False) -> str | None:
    """Timestamp of the database's newest catalogue import; None when it is unreadable or has none."""
    # immutable: the bundle may sit in a read-only site-packages where even a read would create -shm.
    uri = f"{path.resolve().as_uri()}?mode=ro" + ("&immutable=1" if immutable else "")
    try:
        db = sqlite3.connect(uri, uri=True)
        try:
            return db.execute("SELECT MAX(imported_at) FROM imports").fetchone()[0]
        finally:
            db.close()
    except sqlite3.Error:
        return None


def refresh_from_bundle(bundled: Path, dest: Path) -> bool:
    """Replace the catalogue tables of `dest` with the bundled ones when the bundle holds a newer import.

    Done in place, in one write transaction, so running servers see either catalogue and the user's
    cached analyses and answers stay as they are. A database whose own import is newer (the user
    imported a CSV) is kept. Studies re-fetched by get_study revert to the bundled row.
    """
    new = latest_import(bundled, immutable=True)
    if not new or not dest.is_file() or not (latest_import(dest) or "") < new:
        return False
    try:
        repo = Repository(dest)  # brings an older user schema up to this package's version
        # uri=True so ATTACH accepts the read-only, immutable bundle URI
        db = sqlite3.connect(dest.resolve().as_uri(), uri=True, timeout=30)
        try:
            db.execute("ATTACH DATABASE ? AS bundle", (f"{bundled.resolve().as_uri()}?mode=ro&immutable=1",))
            try:
                if db.execute("PRAGMA bundle.user_version").fetchone()[0] != SCHEMA_VERSION:
                    return False
                db.execute("BEGIN IMMEDIATE")
                # Re-check under the write lock: another process may have refreshed meanwhile.
                current = db.execute("SELECT MAX(imported_at) FROM imports").fetchone()[0]
                if current and current >= new:
                    db.rollback()
                    return False
                for table in CATALOGUE_TABLES:
                    columns = ", ".join(
                        r[1] for r in db.execute(f"PRAGMA main.table_info({table})").fetchall()
                    )
                    db.execute(f"DELETE FROM main.{table}")
                    db.execute(f"INSERT INTO main.{table} ({columns}) SELECT {columns} FROM bundle.{table}")
                analysed = db.execute(
                    "SELECT s.body FROM studies s JOIN analyses a ON a.study_id = s.id"
                ).fetchall()
                for (body,) in analysed:  # the bundle's index lacks the user's analysed facts
                    try:
                        repo._index(db, Study.model_validate_json(body))
                    except (KeyError, TypeError, ValueError):
                        continue  # a malformed cached analysis stays unindexed, as it was
                db.commit()
            finally:
                if db.in_transaction:
                    db.rollback()
                db.execute("DETACH DATABASE bundle")
        finally:
            db.close()
        return True
    except Exception:  # noqa: BLE001 - never block startup; the working catalogue stays as it was
        return False
