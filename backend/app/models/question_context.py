from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .semantic_types import FilterItem, SortItem, TimeContext, VersionContext


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
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    sort: list[SortItem] = Field(default_factory=list)
    time_context: TimeContext = Field(default_factory=TimeContext)
    version_context: VersionContext | None = None
    limit: int | None = None
    analysis_mode: str | None = None
