from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


QuestionType = Literal[
    "new",
    "follow_up",
    "new_related",
    "new_unrelated",
    "invalid",
    "clarification_needed",
]
SubjectDomain = Literal[
    "inventory",
    "demand",
    "plan_actual",
    "sales_financial",
    "dimension",
    "unknown",
]
TimeGrain = Literal["day", "week", "month", "version", "unknown"]
FilterOperator = Literal[
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "between",
    "in",
    "like",
    "is_null",
    "not_null",
]
SortOrder = Literal["asc", "desc"]


class FilterItem(BaseModel):
    field: str
    op: FilterOperator
    value: Any


class TimeRange(BaseModel):
    start: str | None = None
    end: str | None = None


class TimeContext(BaseModel):
    grain: TimeGrain = "unknown"
    range: TimeRange | None = None


class VersionContext(BaseModel):
    field: str | None = None
    value: str | None = None


class ContextDelta(BaseModel):
    add_filters: list[FilterItem] = Field(default_factory=list)
    remove_filters: list[str] = Field(default_factory=list)
    replace_metrics: list[str] = Field(default_factory=list)
    replace_dimensions: list[str] = Field(default_factory=list)
    replace_time_context: TimeContext = Field(default_factory=TimeContext)


class SortItem(BaseModel):
    field: str
    order: SortOrder


class QueryPlan(BaseModel):
    question_type: QuestionType
    subject_domain: SubjectDomain
    tables: list[str] = Field(default_factory=list)
    semantic_views: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    join_path: list[str] = Field(default_factory=list)
    time_context: TimeContext = Field(default_factory=TimeContext)
    version_context: VersionContext | None = None
    inherit_context: bool = False
    context_delta: ContextDelta = Field(default_factory=ContextDelta)
    need_clarification: bool = False
    clarification_question: str | None = None
    sort: list[SortItem] = Field(default_factory=list)
    limit: int = 200
    reason: str | None = None
