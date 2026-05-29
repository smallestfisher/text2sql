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

    # Temporary migration field for legacy API shells. It is not a SQL contract.
    subject_domain: str = "unknown"

    @property
    def context_decision(self) -> str:
        if self.context_relation == "ambiguous":
            return "clarification_needed"
        return self.context_relation

    @property
    def user_intent(self) -> str:
        return self.semantic_brief or self.effective_question

    @property
    def knowledge_brief(self) -> str:
        return ""

    @property
    def contract_hint(self) -> dict[str, Any]:
        return {}
