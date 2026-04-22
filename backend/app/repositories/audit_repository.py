from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import AUDIT_DATA_PATH
from backend.app.models.trace import TraceRecord


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    def append(self, record: TraceRecord) -> TraceRecord:
        self.records.append(record)
        return record

    def list_records(self) -> list[TraceRecord]:
        return list(self.records)

    def get_record(self, trace_id: str) -> TraceRecord | None:
        for record in self.records:
            if record.trace_id == trace_id:
                return record
        return None


class FileAuditRepository(InMemoryAuditRepository):
    def __init__(self, path: Path = AUDIT_DATA_PATH) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def append(self, record: TraceRecord) -> TraceRecord:
        appended = super().append(record)
        self._save()
        return appended

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.records = [TraceRecord(**item) for item in payload]

    def _save(self) -> None:
        self.path.write_text(
            json.dumps([item.model_dump(mode="json") for item in self.records], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
