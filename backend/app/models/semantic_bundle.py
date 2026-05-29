from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SemanticDecision = Literal["answerable", "clarification_needed", "invalid"]


class SemanticBundle(BaseModel):
    original_question: str
    effective_question: str
    context_decision: str = "new"
    decision: SemanticDecision = "answerable"
    subject_domain: str = "unknown"
    user_intent: str = ""
    semantic_brief: str = ""
    knowledge_brief: str = ""
    contract_hint: dict[str, Any] = Field(default_factory=dict)
    clarification_question: str | None = None
    reason: str | None = None
    source: str = "llm"
    raw_payload: dict[str, Any] = Field(default_factory=dict)
