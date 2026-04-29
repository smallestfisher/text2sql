from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


AnswerStatus = Literal["ok", "clarification_needed", "invalid", "error", "stub", "chat"]


class AnswerPayload(BaseModel):
    status: AnswerStatus
    summary: str
    detail: str | None = None
    follow_up_hint: str | None = None
