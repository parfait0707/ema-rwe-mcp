"""Persistent, all-candidate comparison exports; never hide incomplete studies."""

import json
import re
import uuid

from .domain import RWEError, atomic_write, now
from .source_types import select_rows

ROW_STUDY_KEYS = ("study_id", "title", "eupas_number", "countries", "data_source_types", "status")


def compact_view(result: dict) -> dict:
    """Tool response without the per-row extraction/answer bodies and assessment lists; the stored
    comparison.json and study_*.json keep everything and comparison_markdown carries the cited table."""
    rows = []
    for row in result["rows"]:
        suitability = row.get("source_suitability") or {}
        rows.append(
            {
                "study": {k: row["study"].get(k) for k in ROW_STUDY_KEYS},
                "status": row.get("status"),
                "match": row.get("match"),
                "error": row.get("error"),
                "source": row.get("source"),
                "source_suitability": {
                    k: suitability.get(k)
                    for k in ("status", "role", "reason", "catalogue_protocol_disjoint", "conflicts")
                },
                "data_source_types": row.get("data_source_types"),
                "data_source_types_status": row.get("data_source_types_status"),
                "protocol_data_sources": [
                    {"value": d["value"], "usage": d["usage"]} for d in row.get("protocol_data_sources", [])
                ],
                "protocol_data_sources_status": row.get("protocol_data_sources_status"),
                "pdf_path": row.get("pdf_path"),
                "json_path": row.get("json_path"),
            }
        )
    return {
        **{k: v for k, v in result.items() if k != "rows"},
        "rows": rows,
        "note": "get_protocol_comparison(detail='full') returns full rows (analysis, answer, assessments).",
    }


def cell(value):
    if isinstance(value, list):
        return "; ".join(cell(v) for v in value) or "未確認"
    if isinstance(value, dict):
        return cell(value.get("value", ""))
    return (
        str(value or "未確認")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def cohort_cells(cohort: dict | None) -> list[str] | None:
    """Flatten the cohort block into labelled lines for the comparison table."""
    if not cohort:
        return None
    lines = []
    for label, key in (("組入", "inclusion_criteria"), ("除外", "exclusion_criteria")):
        for fact in cohort.get(key) or []:
            lines.append(
                f"{label}: {fact['value']} (p. {', '.join(str(e['page']) for e in fact['evidence'])})"
            )
    for label, key in (
        ("インデックス日", "index_date"),
        ("ベースライン", "baseline_period"),
        ("追跡", "follow_up"),
    ):
        fact = cohort.get(key)
        if fact:
            lines.append(
                f"{label}: {fact['value']} (p. {', '.join(str(e['page']) for e in fact['evidence'])})"
            )
    return lines or None


def match_cell(match: dict | None, relations: dict[str, str]) -> str | None:
    """Why a study is a candidate: the requested concept itself, or an analogous concept and its relation."""
    if not match:
        return None
    if match["basis"] == "analogous":
        terms = ", ".join(f"{t}（{relations.get(t.casefold(), 'relation不明')}）" for t in match["terms"])
        return f"類縁概念での一致（依頼された概念そのものではない）: {terms}"
    return "依頼概念での一致: " + ", ".join(match["terms"])


def table(rows, relations: dict[str, str] | None = None):
    if not rows:
        return "比較対象は未確定です。screening_summaryとselection_statusを確認してください。"
    columns = [
        ("一致の根拠", lambda r: match_cell(r.get("match"), relations or {})),
        ("国", lambda r: r["study"]["countries"]),
        ("Data source type", lambda r: r["study"]["data_source_types"]),
        ("希望タイプへの適合性", lambda r: r.get("source_suitability", {}).get("status")),
        (
            "PDF由来のタイプ・用途・判定根拠",
            lambda r: [
                f"{a['value']}: {', '.join(a['types'])}; {a['role']}; {a['basis']}; {a['usage']}; "
                f"連結データ必要={a['requires_linkage']}; {a['definition']} (p. {', '.join(str(e['page']) for e in a['evidence'])})"
                for a in r.get("source_suitability", {}).get("assessments", [])
            ],
        ),
        (
            "公式分類とPDF分類が重ならない",
            lambda r: str(r.get("source_suitability", {}).get("catalogue_protocol_disjoint", False)),
        ),
        (
            "PDF記載データソース（使用状態付き）",
            lambda r: [f"{v['value']} ({v['usage']})" for v in r.get("analysis", {}).get("data_sources", [])],
        ),
        (
            "Study design",
            lambda r: r.get("analysis", {}).get("study_design") or r["study"].get("study_designs"),
        ),
        ("対象集団・年齢", lambda r: r.get("analysis", {}).get("population")),
        (
            "コホート定義（組入・除外・インデックス日・ベースライン・追跡）",
            lambda r: cohort_cells(r.get("analysis", {}).get("cohort")),
        ),
        (
            "設計図（ページ・時間窓）",
            lambda r: (
                (
                    [f"figure p. {', '.join(map(str, ds.get('figure_pages', [])))}"]
                    if ds.get("figure_pages")
                    else []
                )
                + [
                    f"{w['value']} (p. {', '.join(str(e['page']) for e in w['evidence'])})"
                    for w in ds.get("time_windows", [])
                ]
                if (ds := ((r.get("analysis", {}).get("cohort") or {}).get("design_schema") or {}))
                else None
            ),
        ),
        ("疾患定義・コード・観察期間・除外条件", lambda r: r.get("analysis", {}).get("disease_definitions")),
        ("問い合わせへの回答", lambda r: r.get("answer", {}).get("answers")),
        (
            "根拠ページ",
            lambda r: sorted(
                {e["page"] for f in r.get("answer", {}).get("answers", []) for e in f["evidence"]}
            ),
        ),
        (
            "未記載・未確認事項",
            lambda r: (
                r.get("answer", {}).get("missing_information", [])
                + r.get("analysis", {}).get("missing_information", [])
            ),
        ),
        ("出典PDF", lambda r: f"[PDF]({r['source']['document_url']})" if r.get("source") else None),
        ("PDF ID", lambda r: r.get("source", {}).get("protocol_id")),
        ("取得エラー", lambda r: r.get("error", {}).get("message") or "なし"),
        ("処理状態", lambda r: r["status"]),
    ]
    lines = [
        "| 比較項目 | " + " | ".join(cell(r["study"]["title"]) for r in rows) + " |",
        "|---|" + "---|" * len(rows),
    ]
    lines += ["| " + label + " | " + " | ".join(cell(fn(r)) for r in rows) + " |" for label, fn in columns]
    return "\n".join(lines)


class Comparisons:
    def __init__(self, service):
        self.service = service
        self.root = service.settings.db_path.parent / "comparisons"

    def path(self, comparison_id):
        if not re.fullmatch(r"cmp_[a-f0-9]{32}", comparison_id):
            raise RWEError("INVALID_INPUT", "Use a comparison_id returned by compare_protocols.")
        return self.root / comparison_id / "comparison.json"

    async def prepare(self, question, candidates, search):
        if not 1 <= len(candidates) <= self.service.settings.max_screening_studies:
            raise RWEError("NEEDS_NARROWING", "Narrow candidates to the configured PDF screening limit.")
        cid = "cmp_" + uuid.uuid4().hex
        result = {
            "comparison_id": cid,
            "question": question,
            "created_at": now(),
            "search": search,
            "rows": [
                {
                    "study": row,
                    "match": {"basis": row["match_basis"], "terms": row["matched_terms"]},
                    "status": "pending",
                }
                for row in candidates
            ],
        }
        self.write(result)
        for row in result["rows"]:
            try:
                _, row["source"] = await self.service._context(row["study"]["study_id"])
                row["study"] = self.service.repo.get(row["study"]["study_id"]).model_dump(exclude={"tabs"})
                row["status"] = "needs_client_processing"
            except RWEError as exc:
                row.update(status="error", error=exc.as_dict()["error"])
            except OSError:
                row.update(
                    status="error",
                    error={"code": "CACHE_ERROR", "message": "PDF filesystem operation failed."},
                )
            self.write(result)
        return self.collect(cid)

    def write(self, result):
        shown = select_rows(result)
        folder = self.path(result["comparison_id"]).parent
        for row in result["rows"]:
            row["data_source_types"] = row["study"]["data_source_types"]
            row["data_source_types_status"] = "available" if row["data_source_types"] else "not_provided"
            row["protocol_data_sources"] = row.get("analysis", {}).get("data_sources", [])
            row["protocol_data_sources_status"] = "extracted" if "analysis" in row else "not_analyzed"
            row["pdf_path"] = (
                str((self.service.archive.directory / row["source"]["local_filename"]).resolve())
                if row.get("source")
                else None
            )
            row["json_path"] = str(folder / f"study_{row['study']['study_id']}.json")
            atomic_write(
                folder / f"study_{row['study']['study_id']}.json",
                json.dumps(row, ensure_ascii=False, indent=2),
            )
        result["json_path"] = str(folder / "comparison.json")
        result["markdown_path"] = str(folder / "comparison.md")
        relations = {t["term"].casefold(): t["relation"] for t in result["search"].get("analogous_terms", [])}
        result["comparison_markdown"] = table(shown, relations)
        result["comparison_markdown"] += "\n\n全一次判定結果（省略・エラーを含む）:\n\n" + "\n".join(
            f"- Study {r['study_id']}: {r['suitability']}; {r['status']}; "
            f"{'比較表に掲載' if r['in_comparison'] else '比較表の対象外／未選択'}"
            + (f"; {cell(r['error']['message'])}" if r.get("error") else "")
            for r in result["screening_summary"]
        )
        atomic_write(folder / "comparison.md", result["comparison_markdown"])
        atomic_write(folder / "comparison.json", json.dumps(result, ensure_ascii=False, indent=2))

    def collect(self, cid, selected_study_ids=None, detail="compact"):
        result = self._collect(cid, selected_study_ids)
        return result if detail == "full" else compact_view(result)

    def _collect(self, cid, selected_study_ids=None):
        try:
            result = json.loads(self.path(cid).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RWEError("COMPARISON_NOT_FOUND", "Comparison was not found locally.") from exc
        except (ValueError, OSError) as exc:
            raise RWEError("CACHE_ERROR", "Comparison JSON could not be read.") from exc
        todo = []
        for row in result["rows"]:
            source = row.get("source")
            if not source:
                continue
            row.pop("analysis", None)
            row.pop("answer", None)
            sid, pid = row["study"]["study_id"], source["protocol_id"]
            try:
                self.service.archive.load(pid)
            except RWEError as exc:
                row.update(status="error", error=exc.as_dict()["error"])
                continue
            row.pop("error", None)
            analysis = self.service.repo.analysis(sid)
            if analysis and analysis["source"]["fingerprint"] != source["fingerprint"]:
                row.update(
                    status="error",
                    error={
                        "code": "COMPARISON_SOURCE_CHANGED",
                        "message": "PDF/extraction configuration changed. Run compare_protocols again for a new comparison.",
                    },
                )
                continue
            if analysis:
                row["analysis"] = analysis["analysis"]
            else:
                todo.append({"tool": "analyze_protocol", "arguments": {"study_id": sid}})
            with self.service.repo.connection() as db:
                cached = db.execute(
                    "SELECT body FROM protocol_answers WHERE cache_key=?",
                    (self.service.explorer.key(pid, result["question"]),),
                ).fetchone()
            if cached:
                row["answer"] = json.loads(cached[0])["answer"]
            else:
                todo.append(
                    {
                        "tool": "research_protocol",
                        "arguments": {"protocol_id": pid, "question": result["question"]},
                    }
                )
            row["status"] = "complete" if "analysis" in row and "answer" in row else "needs_processing"
        result["status"] = (
            "complete" if all(r["status"] == "complete" for r in result["rows"]) else "incomplete"
        )
        result["pending_tools"] = todo
        select_rows(result, selected_study_ids)
        result["instruction"] = (
            "Execute every pending tool for ALL rows. If needs_client_extraction, read all next_offset batches "
            "and cache_protocol_analysis; if needs_client_exploration, inspect relevant text and cache_protocol_answer. "
            "Then call get_protocol_comparison to update ALL JSON files and the table. Present comparison_markdown with "
            "citations; do not describe incomplete exports as finished. Report every error/missing protocol. "
            "Report source_suitability by definition role; prefer matched sources, distinguish linked/inferred/other/unknown. "
            "The processing status complete does not imply every question was answered. If needs_selection, show "
            "screening_summary and ask the user to narrow or select eligible Study IDs within max_comparison_studies, "
            "then pass selected_study_ids to get_protocol_comparison. Never silently take the first N. "
            "All screened studies retain PDF/JSON, even when not displayed. "
            "For failed or interrupted PDF preparation, resolve the error and run compare_protocols again."
        )
        self.write(result)
        return result
