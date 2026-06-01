from __future__ import annotations

from pydantic import BaseModel, Field

from .semantic_types import ContextDelta, FilterItem, QuestionType, SortItem, SubjectDomain, TimeContext, VersionContext


class SqlGenerationContext(BaseModel):
    question_type: QuestionType
    subject_domain: SubjectDomain
    tables: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    join_path: list[str] = Field(default_factory=list)
    sort: list[SortItem] = Field(default_factory=list)
    limit: int = 200
    time_context: TimeContext = Field(default_factory=TimeContext)
    version_context: VersionContext | None = None
    analysis_mode: str | None = None
    inherit_context: bool = False
    context_delta: ContextDelta = Field(default_factory=ContextDelta)
    need_clarification: bool = False
    clarification_question: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    semantic_brief: str | None = None
