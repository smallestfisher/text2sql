from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    name: str
    status: str
    detail: str | None = None
    metadata: dict = Field(default_factory=dict)


class TraceRecord(BaseModel):
    trace_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    steps: list[TraceStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
