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
# Engineering enum for LLM output space / validation — not business rules.
# Keep in sync with SubjectDomain literals above.
SUPPORTED_SUBJECT_DOMAINS: frozenset[str] = frozenset(
    {
        "inventory",
        "demand",
        "plan_actual",
        "sales_financial",
        "dimension",
        "unknown",
    }
)
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
    "latest_n",
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


class SortItem(BaseModel):
    field: str
    order: SortOrder


class ContextDelta(BaseModel):
    add_filters: list[FilterItem] = Field(default_factory=list)
    remove_filters: list[str] = Field(default_factory=list)
    clear_filters: bool = False
    replace_entities: list[str] = Field(default_factory=list)
    replace_metrics: list[str] = Field(default_factory=list)
    replace_dimensions: list[str] = Field(default_factory=list)
    replace_sort: list[SortItem] = Field(default_factory=list)
    replace_time_context: TimeContext = Field(default_factory=TimeContext)
    replace_version_context: VersionContext | None = None
    replace_limit: int | None = None
    replace_analysis_mode: str | None = None

