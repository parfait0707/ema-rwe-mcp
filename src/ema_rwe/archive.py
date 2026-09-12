"""User-requested retained PDFs, addressed by immutable ID; not the expiring HTTP cache."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .domain import RWEError, now


class ProtocolArchive:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, protocol_id: str, suffix: str) -> Path:
        if not re.fullmatch(r"pdf_\d{1,20}_[a-f0-9]{64}", protocol_id):
            raise RWEError("INVALID_INPUT", "Invalid protocol_id; use an ID returned by get_protocol.")
        return self.directory / (protocol_id + suffix)

    def _atomic(self, path: Path, content: bytes):
        fd, temp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    def save(self, study_id: str, pdf: bytes, metadata: dict) -> dict:
        if not re.fullmatch(r"\d{1,20}", study_id) or not pdf.lstrip().startswith(b"%PDF-"):
            raise RWEError("INVALID_INPUT", "Cannot archive invalid study ID/PDF.")
        digest = hashlib.sha256(pdf).hexdigest()
        pid = f"pdf_{study_id}_{digest}"
        path = self._path(pid, ".pdf")
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            self._atomic(path, pdf)
        record = {
            **metadata,
            "study_id": study_id,
            "protocol_id": pid,
            "sha256": digest,
            "local_filename": pid + ".pdf",
            "archived_at": now(),
            "retention": "until_user_deletes",
        }
        self._atomic(self._path(pid, ".json"), json.dumps(record, ensure_ascii=False).encode())
        return record

    def load(self, protocol_id: str) -> tuple[bytes, dict]:
        try:
            pdf = self._path(protocol_id, ".pdf").read_bytes()
            record = json.loads(self._path(protocol_id, ".json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RWEError(
                "LOCAL_PDF_NOT_FOUND", "Archived PDF/manifest missing; retrieve the protocol again."
            ) from exc
        if hashlib.sha256(pdf).hexdigest() != protocol_id.rsplit("_", 1)[1]:
            raise RWEError("LOCAL_PDF_CORRUPT", "Archived PDF checksum mismatch.")
        return pdf, record

    def list(self, study_id: str) -> list[dict]:
        if not re.fullmatch(r"\d{1,20}", study_id):
            raise RWEError("INVALID_INPUT", "Numeric study_id required.")
        records = []
        for path in self.directory.glob(f"pdf_{study_id}_*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if self._path(record["protocol_id"], ".pdf").exists():
                    records.append(record)
            except (ValueError, KeyError, OSError):
                continue
        return sorted(records, key=lambda r: r["archived_at"], reverse=True)
