from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .api import ChatResponse
from .auth import UserContext


class EvaluationCase(BaseModel):
    id: str
    question: str
    session_questions: list[str] = Field(default_factory=list)
    scenario: str | None = None
    coverage_tags: list[str] = Field(default_factory=list)
    expected_domain: str | None = None
    expected_question_type: str | None = None
    expected_metrics: list[str] = Field(default_factory=list)
    unexpected_metrics: list[str] = Field(default_factory=list)
    expected_dimensions: list[str] = Field(default_factory=list)
    unexpected_dimensions: list[str] = Field(default_factory=list)
    expected_sort_fields: list[str] = Field(default_factory=list)
    unexpected_sort_fields: list[str] = Field(default_factory=list)
    expected_filter_fields: list[str] = Field(default_factory=list)
    expected_semantic_views: list[str] = Field(default_factory=list)
    expected_status: str | None = None
    expected_reason_code: str | None = None
    expected_warnings_contains: list[str] = Field(default_factory=list)
    user_context: UserContext | None = None
    notes: str | None = None


class EvaluationCaseCollection(BaseModel):
    cases: list[EvaluationCase] = Field(default_factory=list)
    count: int = 0


class EvaluationRunRequest(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    user_context: UserContext | None = None


class EvaluationResultItem(BaseModel):
    case_id: str
    question: str
    scenario: str | None = None
    coverage_tags: list[str] = Field(default_factory=list)
    effective_user_id: str | None = None
    classification_question_type: str | None = None
    classification_domain: str | None = None
    answer_status: str | None = None
    actual_reason_code: str | None = None
    actual_metrics: list[str] = Field(default_factory=list)
    actual_dimensions: list[str] = Field(default_factory=list)
    actual_filter_fields: list[str] = Field(default_factory=list)
    actual_semantic_views: list[str] = Field(default_factory=list)
    actual_warnings: list[str] = Field(default_factory=list)
    plan_valid: bool = False
    sql_valid: bool = False
    executed: bool = False
    passed: bool = False
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvaluationRunRecord(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    case_count: int
    passed_count: int
    failed_count: int
    items: list[EvaluationResultItem] = Field(default_factory=list)


class EvaluationDimensionSummary(BaseModel):
    key: str
    total: int
    passed: int
    failed: int


class EvaluationSummary(BaseModel):
    run_count: int
    case_count: int
    passed_count: int
    failed_count: int
    by_domain: list[EvaluationDimensionSummary] = Field(default_factory=list)
    by_question_type: list[EvaluationDimensionSummary] = Field(default_factory=list)
    by_answer_status: list[EvaluationDimensionSummary] = Field(default_factory=list)


class EvaluationReplayRequest(BaseModel):
    user_id: str | None = None
    reuse_original_user: bool = True
    include_prior_context: bool = True


class RuntimeQueryLogMaterializeCaseRequest(BaseModel):
    case_id: str | None = None
    scenario: str | None = None
    coverage_tags: list[str] = Field(default_factory=list)
    user_id: str | None = None
    reuse_original_user: bool = True
    include_prior_context: bool = True
    notes: str | None = None


class EvaluationReplayDiff(BaseModel):
    classification_changed: bool = False
    question_type_changed: bool = False
    subject_domain_changed: bool = False
    answer_status_changed: bool = False
    plan_valid_changed: bool = False
    plan_risk_level_changed: bool = False
    sql_valid_changed: bool = False
    sql_risk_level_changed: bool = False
    execution_status_changed: bool = False
    sql_changed: bool = False
    prompt_context_changed: bool = False
    original_prompt_context_summary: dict = Field(default_factory=dict)
    replay_prompt_context_summary: dict = Field(default_factory=dict)
    metrics_added: list[str] = Field(default_factory=list)
    metrics_removed: list[str] = Field(default_factory=list)
    dimensions_added: list[str] = Field(default_factory=list)
    dimensions_removed: list[str] = Field(default_factory=list)
    filter_fields_added: list[str] = Field(default_factory=list)
    filter_fields_removed: list[str] = Field(default_factory=list)
    semantic_views_added: list[str] = Field(default_factory=list)
    semantic_views_removed: list[str] = Field(default_factory=list)
    plan_risk_flags_added: list[str] = Field(default_factory=list)
    plan_risk_flags_removed: list[str] = Field(default_factory=list)
    sql_risk_flags_added: list[str] = Field(default_factory=list)
    sql_risk_flags_removed: list[str] = Field(default_factory=list)


class EvaluationReplayResult(BaseModel):
    source_type: Literal["evaluation_case", "runtime_query_log"]
    source_id: str
    question: str
    session_questions: list[str] = Field(default_factory=list)
    replay_user: UserContext | None = None
    original_trace_id: str | None = None
    original_session_id: str | None = None
    original_user_id: str | None = None
    original_response: ChatResponse | None = None
    diff: EvaluationReplayDiff | None = None
    response: ChatResponse
