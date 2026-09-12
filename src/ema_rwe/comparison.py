"""Persistent, all-candidate comparison exports; never hide incomplete studies."""

import json
import re
import tempfile
import uuid

from .domain import RWEError, now


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as f:
            temporary = type(path)(f.name)
            f.write(text)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


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


def table(rows):
    columns = [
        ("国", lambda r: r["study"]["countries"]),
        ("Data source type", lambda r: r["study"]["data_source_types"]),
        (
            "PDF記載データソース（使用状態付き）",
            lambda r: [f"{v['value']} ({v['usage']})" for v in r.get("analysis", {}).get("data_sources", [])],
        ),
        (
            "Study design",
            lambda r: r.get("analysis", {}).get("study_design") or r["study"].get("study_designs"),
        ),
        ("対象集団・年齢", lambda r: r.get("analysis", {}).get("population")),
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
        if not 1 <= len(candidates) <= 5:
            raise RWEError("NEEDS_NARROWING", "Compare all candidates only after narrowing to 1..5.")
        cid = "cmp_" + uuid.uuid4().hex
        result = {
            "comparison_id": cid,
            "question": question,
            "created_at": now(),
            "search": search,
            "rows": [{"study": row, "status": "pending"} for row in candidates],
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
        result["comparison_markdown"] = table(result["rows"])
        atomic_write(folder / "comparison.md", result["comparison_markdown"])
        atomic_write(folder / "comparison.json", json.dumps(result, ensure_ascii=False, indent=2))

    def collect(self, cid):
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
            if analysis and analysis["source"]["fingerprint"] == source["fingerprint"]:
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
        result["instruction"] = (
            "Execute every pending tool for ALL rows. If needs_client_extraction, read all next_offset batches "
            "and cache_protocol_analysis; if needs_client_exploration, inspect relevant text and cache_protocol_answer. "
            "Then call get_protocol_comparison to update ALL JSON files and the table. Present comparison_markdown with "
            "citations; do not describe incomplete exports as finished. Report every error/missing protocol. "
            "For failed or interrupted PDF preparation, resolve the error and run compare_protocols again."
        )
        self.write(result)
        return result
