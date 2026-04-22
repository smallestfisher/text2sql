from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import FEEDBACK_DATA_PATH
from backend.app.models.feedback import FeedbackRecord


class InMemoryFeedbackRepository:
    def __init__(self) -> None:
        self.records: list[FeedbackRecord] = []

    def append(self, record: FeedbackRecord) -> FeedbackRecord:
        self.records.append(record)
        return record

    def list_records(self) -> list[FeedbackRecord]:
        return list(self.records)


class FileFeedbackRepository(InMemoryFeedbackRepository):
    def __init__(self, path: Path = FEEDBACK_DATA_PATH) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def append(self, record: FeedbackRecord) -> FeedbackRecord:
        appended = super().append(record)
        self._save()
        return appended

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.records = [FeedbackRecord(**item) for item in payload]

    def _save(self) -> None:
        self.path.write_text(
            json.dumps([item.model_dump(mode="json") for item in self.records], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
