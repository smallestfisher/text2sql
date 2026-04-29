from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel


AnswerStatus = Literal["ok", "clarification_needed", "invalid", "error", "chat"]
_KNOWN_ANSWER_STATUSES = {"ok", "clarification_needed", "invalid", "error", "chat"}


def normalize_answer_status(status: str | None, *, executed: bool = False) -> AnswerStatus:
    if status in _KNOWN_ANSWER_STATUSES:
        return cast(AnswerStatus, status)
    return "ok" if executed else "error"


class AnswerPayload(BaseModel):
    status: AnswerStatus
    summary: str
    detail: str | None = None
    follow_up_hint: str | None = None
