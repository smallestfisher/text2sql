from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ProgressEventType = Literal["accepted", "stage", "completed", "failed"]


class ProgressEvent(BaseModel):
    trace_id: str
    type: ProgressEventType
    stage: str
    status: str
    detail: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict = Field(default_factory=dict)
