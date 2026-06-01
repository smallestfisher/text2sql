from __future__ import annotations

from pydantic import BaseModel, Field

from .semantic_types import ContextDelta, QuestionType, SubjectDomain


class QuestionClassification(BaseModel):
    question_type: QuestionType
    subject_domain: SubjectDomain
    inherit_context: bool = False
    confidence: float = 0.0
    reason: str | None = None
    reason_code: str | None = None
    suggested_reply: str | None = None
    context_delta: ContextDelta = Field(default_factory=ContextDelta)
    need_clarification: bool = False
    clarification_question: str | None = None
