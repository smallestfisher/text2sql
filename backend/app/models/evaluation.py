from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .auth import UserContext


class EvaluationCase(BaseModel):
    id: str
    question: str
    session_questions: list[str] = Field(default_factory=list)
    expected_domain: str | None = None
    expected_question_type: str | None = None
    expected_metrics: list[str] = Field(default_factory=list)
    expected_status: str | None = None
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
    classification_question_type: str | None = None
    classification_domain: str | None = None
    answer_status: str | None = None
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
