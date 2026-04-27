from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal

from .answer import AnswerPayload
from .auth import UserContext
from .classification import QuestionClassification, QueryIntent
from .query_plan import QueryPlan
from .retrieval import RetrievalContext
from .session_state import SessionState
from .trace import TraceRecord


class PlanRequest(BaseModel):
    question: str
    session_id: str | None = None
    session_state: SessionState | None = None
    user_context: UserContext | None = None


class PlanValidationRequest(BaseModel):
    query_plan: QueryPlan


class SqlGenerationRequest(BaseModel):
    query_plan: QueryPlan
    user_context: UserContext | None = None


class SqlExecutionRequest(BaseModel):
    sql: str
    user_context: UserContext | None = None


class PlanResponse(BaseModel):
    classification: QuestionClassification
    query_intent: QueryIntent
    query_plan: QueryPlan
    domain_summary: dict
    warnings: list[str]


class ClassificationResponse(BaseModel):
    classification: QuestionClassification
    query_intent: QueryIntent
    warnings: list[str]


class RetrievalPreviewResponse(BaseModel):
    query_intent: QueryIntent
    retrieval: RetrievalContext


RiskLevel = Literal["low", "medium", "high"]


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    risk_level: RiskLevel = "low"
    risk_flags: list[str] = Field(default_factory=list)


class SqlResponse(BaseModel):
    query_plan: QueryPlan
    sql: str | None
    validation: ValidationResponse


ExecutionStatus = Literal[
    "ok",
    "empty_result",
    "truncated",
    "db_error",
    "timeout",
    "permission_denied",
    "sql_missing",
    "not_configured",
    "blocked",
]


class ExecutionResponse(BaseModel):
    executed: bool
    status: ExecutionStatus
    sql: str | None
    row_count: int
    columns: list[str]
    rows: list[dict]
    errors: list[str]
    warnings: list[str]
    elapsed_ms: int | None = None
    error_category: str | None = None
    truncated: bool = False


class ChatResponse(BaseModel):
    classification: QuestionClassification
    query_intent: QueryIntent
    retrieval: RetrievalContext | None = None
    trace: TraceRecord | None = None
    answer: AnswerPayload | None = None
    query_plan: QueryPlan
    sql: str | None
    plan_validation: ValidationResponse
    sql_validation: ValidationResponse
    execution: ExecutionResponse | None
    next_session_state: SessionState
