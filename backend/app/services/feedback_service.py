from __future__ import annotations

import uuid

from backend.app.models.feedback import FeedbackRecord, FeedbackRequest


class FeedbackService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def submit(self, request: FeedbackRequest) -> FeedbackRecord:
        record = FeedbackRecord(
            id=f"fb_{uuid.uuid4().hex[:12]}",
            session_id=request.session_id,
            trace_id=request.trace_id,
            user_id=request.user_id,
            feedback_type=request.feedback_type,
            comment=request.comment,
        )
        return self.repository.append(record)

    def list_records(self) -> list[FeedbackRecord]:
        return self.repository.list_records()
