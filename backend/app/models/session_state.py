from __future__ import annotations

from pydantic import BaseModel, Field

from .context_summary import ContextSummary
from .semantic_types import ContextDelta, FilterItem, QuestionType, SortItem, SubjectDomain, TimeContext, VersionContext


class QueryTurnRecord(BaseModel):
    question: str | None = None
    effective_question: str | None = None
    summary: str | None = None
    semantic_brief: str | None = None


class PendingClarification(BaseModel):
    original_question: str
    clarification_question: str
    effective_question: str | None = None
    semantic_brief: str | None = None
    reason: str | None = None


class SessionStateUpdate(BaseModel):
    question_type: QuestionType
    subject_domain: SubjectDomain
    entities: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    sort: list[SortItem] = Field(default_factory=list)
    limit: int | None = None
    time_context: TimeContext = Field(default_factory=TimeContext)
    version_context: VersionContext | None = None
    analysis_mode: str | None = None
    inherit_context: bool = False
    context_delta: ContextDelta = Field(default_factory=ContextDelta)
    need_clarification: bool = False
    clarification_question: str | None = None
    semantic_brief: str | None = None


class SessionState(BaseModel):
    session_id: str
    semantic_release_id: str | None = None
    topic: str | None = None
    conversation_summary: str | None = None
    subject_domain: SubjectDomain = "unknown"
    entities: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    sort: list[SortItem] = Field(default_factory=list)
    limit: int | None = None
    time_context: TimeContext | None = None
    version_context: VersionContext | None = None
    analysis_mode: str | None = None
    last_question_type: str | None = None
    last_context_summary: ContextSummary | None = None
    last_sql: str | None = None
    last_result_shape: str | None = None
    last_semantic_brief: str | None = None
    last_effective_question: str | None = None
    recent_turns: list[QueryTurnRecord] = Field(default_factory=list)
    pending_clarification: PendingClarification | None = None
