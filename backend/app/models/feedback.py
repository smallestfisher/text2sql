from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


FeedbackType = Literal["correct", "incorrect", "clarification", "other"]


class FeedbackRecord(BaseModel):
    id: str
    session_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    feedback_type: FeedbackType
    comment: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FeedbackRequest(BaseModel):
    session_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    feedback_type: FeedbackType
    comment: str | None = None


class FeedbackCollectionResponse(BaseModel):
    feedbacks: list[FeedbackRecord] = Field(default_factory=list)
    count: int = 0


class FeedbackTypeSummary(BaseModel):
    feedback_type: FeedbackType
    count: int


class FeedbackSummary(BaseModel):
    total: int = 0
    by_type: list[FeedbackTypeSummary] = Field(default_factory=list)
