from __future__ import annotations

from pydantic import BaseModel, Field

from .query_plan import ContextDelta, FilterItem, QuestionType, SortItem, SubjectDomain, TimeContext, VersionContext


class SemanticParse(BaseModel):
    normalized_question: str
    matched_metrics: list[str] = Field(default_factory=list)
    matched_entities: list[str] = Field(default_factory=list)
    requested_dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    time_context: TimeContext = Field(default_factory=TimeContext)
    version_context: VersionContext | None = None
    requested_sort: list[SortItem] = Field(default_factory=list)
    requested_limit: int | None = None
    analysis_mode: str | None = None
    subject_domain: SubjectDomain = "unknown"
    has_follow_up_cue: bool = False
    has_explicit_slots: bool = False


class QuestionClassification(BaseModel):
    question_type: QuestionType
    subject_domain: SubjectDomain
    inherit_context: bool = False
    confidence: float = 0.0
    reason: str | None = None
    reason_code: str | None = None
    context_delta: ContextDelta = Field(default_factory=ContextDelta)
    need_clarification: bool = False
    clarification_question: str | None = None
