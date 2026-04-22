from __future__ import annotations

from collections import Counter
import uuid

from backend.app.models.feedback import (
    FeedbackCollectionResponse,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackSummary,
    FeedbackTypeSummary,
)


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

    def list_records(
        self,
        session_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> FeedbackCollectionResponse:
        feedbacks = self.repository.list_records(
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            limit=limit,
        )
        return FeedbackCollectionResponse(feedbacks=feedbacks, count=len(feedbacks))

    def summarize(
        self,
        session_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> FeedbackSummary:
        feedbacks = self.repository.list_records(
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            limit=limit,
        )
        counter = Counter(item.feedback_type for item in feedbacks)
        return FeedbackSummary(
            total=len(feedbacks),
            by_type=[
                FeedbackTypeSummary(feedback_type=feedback_type, count=count)
                for feedback_type, count in sorted(counter.items())
            ],
        )
