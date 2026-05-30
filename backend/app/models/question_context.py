from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


QuestionDecision = Literal["answerable", "clarification_needed", "invalid"]
ContextRelation = Literal["new", "follow_up", "ambiguous"]


class QuestionContext(BaseModel):
    original_question: str
    effective_question: str
    context_relation: ContextRelation = "new"
    decision: QuestionDecision = "answerable"
    conversation_summary: str = ""
    semantic_brief: str = ""
    clarification_question: str | None = None
    reason: str | None = None
    source: str = "llm"
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    subject_domain: str = "unknown"
