from __future__ import annotations

from pydantic import BaseModel
from typing import Literal

from .answer import AnswerPayload
from .auth import UserContext
from .classification import QuestionClassification, SemanticParse
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
    semantic_parse: SemanticParse
    query_plan: QueryPlan
    semantic_summary: dict
    warnings: list[str]


class ClassificationResponse(BaseModel):
    classification: QuestionClassification
    semantic_parse: SemanticParse
    warnings: list[str]


class RetrievalPreviewResponse(BaseModel):
    semantic_parse: SemanticParse
    retrieval: RetrievalContext


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


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
    semantic_parse: SemanticParse
    retrieval: RetrievalContext | None = None
    trace: TraceRecord | None = None
    answer: AnswerPayload | None = None
    query_plan: QueryPlan
    sql: str | None
    plan_validation: ValidationResponse
    sql_validation: ValidationResponse
    execution: ExecutionResponse | None
    next_session_state: SessionState
