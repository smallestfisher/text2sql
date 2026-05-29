from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .query_plan import FilterItem, QueryPlan, SortItem, SubjectDomain, TimeContext, VersionContext


class QueryTurnRecord(BaseModel):
    question: str | None = None
    effective_question: str | None = None
    summary: str | None = None
    semantic_brief: str | None = None
    query_contract: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    session_id: str
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
    last_query_plan: QueryPlan | None = None
    last_sql: str | None = None
    last_result_shape: str | None = None
    last_semantic_brief: str | None = None
    last_effective_question: str | None = None
    recent_turns: list[QueryTurnRecord] = Field(default_factory=list)
