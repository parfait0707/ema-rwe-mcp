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
from .selection import filter_rows
from .terminology import search_units
from .vocabulary import canonical, expand


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] > 3:
                raise RWEError("DATABASE_ERROR", "Database schema is newer than this server supports.")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS studies (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS study_fts USING fts5(
                    id UNINDEXED, title, metadata, design, definitions, data_sources,
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
                PRAGMA user_version=3;
            """)

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
            "INSERT INTO study_fts VALUES (?,?,?,?,?,?)",
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
                        values(a.get("exposure")),
                        values(a.get("outcomes")),
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
    ) -> list[dict]:
        if (limit is not None and not 1 <= limit <= 20) or len(query) > 2000:
            raise RWEError("INVALID_INPUT", "limit must be 1..20; query must be at most 2000 characters.")
        expansion = expand(query, synonyms, codes)
        tokens = [canonical(t) for t in search_units(query, expansion) if canonical(t)]
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        with self.connection() as db:
            if query.strip() and not match:
                return []
            if match:
                rows = db.execute(
                    """SELECT s.body, bm25(study_fts,0,3,1,5,4,3) AS rank
                    FROM study_fts JOIN studies s ON s.id=study_fts.id
                    WHERE study_fts MATCH ? ORDER BY rank, s.id""",
                    (match,),
                ).fetchall()
            else:
                rows = db.execute("SELECT body, 0 AS rank FROM studies ORDER BY id").fetchall()
        results = []
        for row in rows:
            study = Study.model_validate_json(row["body"])
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
            )
            results.append(result)
            if limit is not None and len(results) >= limit:
                break
        return results


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


def import_csv(repo: Repository, path: Path, column_map: dict[str, str] | None = None) -> dict:
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
    if any(
        not selected.get(key) or selected[key] not in fieldnames
        for key in ("study_id", "title", "study_type")
    ):
        raise RWEError(
            "CSV_SCHEMA_ERROR",
            "Required CSV columns: Study ID, Official title and acronym, "
            "Study type. Use --column-map for alternate headers. Found: " + str(fieldnames),
        )
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
            v[key] = [x.strip() for x in re.split(r"[|;\n]+", v.get(key, "")) if x.strip()]
        if other_sources_column:
            v["catalogue_data_sources"] += [
                x.strip() for x in re.split(r"[|;\n]+", row.get(other_sources_column, "") or "") if x.strip()
            ]
            v["catalogue_data_sources"] = list(dict.fromkeys(v["catalogue_data_sources"]))
        if other_design_column:
            v["study_designs"] += [
                x.strip() for x in re.split(r"[|;\n]+", row.get(other_design_column, "") or "") if x.strip()
            ]
            v["study_designs"] = list(dict.fromkeys(v["study_designs"]))
        v["darwin_eu"] = {"yes": True, "true": True, "1": True, "no": False, "false": False, "0": False}.get(
            v.get("darwin_eu", "").lower()
        )
        v["eupas_number"] = v.get("eupas_number") or None
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
            if not selected.get("data_source_types") and old:
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
    if not selected.get("data_source_types"):
        schema_warnings.append("CSV has no Data source type column; detail-page enrichment is required.")
    if duplicate_headers:
        schema_warnings.append("Duplicate non-indexed CSV headers: " + ", ".join(duplicate_headers))
    return {
        "imported": len(studies),
        "skipped_out_of_scope": skipped,
        "checksum": checksum,
        "mode": "upsert; records absent from this export are retained",
        "schema_warnings": schema_warnings,
    }
