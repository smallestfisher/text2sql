from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field

from .auth import RoleRecord, UserCollectionResponse
from .conversation import ChatSession
from .evaluation import EvaluationSummary
from .example_library import ExampleRecord, ExampleTemplateRecord
from .feedback import FeedbackSummary
from .session_state import SessionState


class MetadataDocument(BaseModel):
    name: str
    path: str
    content: dict | list | str


class MetadataOverview(BaseModel):
    semantic_version: str | None
    semantic_domains: list[str]
    table_count: int
    example_count: int
    trace_count: int


class AdminMetricChangeRecord(BaseModel):
    total: int
    today: int
    yesterday: int
    delta: int


class AdminMetricsSummary(BaseModel):
    users: AdminMetricChangeRecord
    sessions: AdminMetricChangeRecord
    query_logs: AdminMetricChangeRecord
    feedbacks: AdminMetricChangeRecord
    generated_at: datetime


class ExampleCollectionResponse(BaseModel):
    examples: list[ExampleRecord]
    count: int


class ExampleMutationResponse(BaseModel):
    created: bool | None = None
    updated: bool | None = None
    example: ExampleRecord
    template: ExampleTemplateRecord
    count: int | None = None


class RuntimeSessionCollectionResponse(BaseModel):
    sessions: list[ChatSession]
    count: int


class SessionSnapshotRecord(BaseModel):
    snapshot_id: str
    session_id: str
    trace_id: str | None = None
    state: SessionState
    created_at: datetime


class RuntimeQueryLogRecord(BaseModel):
    trace_id: str
    session_id: str | None = None
    user_id: str | None = None
    question: str | None = None
    effective_question: str | None = None
    context_relation: str | None = None
    question_decision: str | None = None
    conversation_summary: str | None = None
    semantic_brief: str | None = None
    question_context: dict = Field(default_factory=dict)
    question_type: str | None = None
    subject_domain: str | None = None
    answer_status: str | None = None
    context_valid: bool | None = None
    context_risk_level: str | None = None
    context_risk_flags: list[str] = Field(default_factory=list)
    sql_valid: bool | None = None
    sql_risk_level: str | None = None
    sql_risk_flags: list[str] = Field(default_factory=list)
    executed: bool | None = None
    row_count: int | None = None
    total_elapsed_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)
    prompt_context_summary: dict = Field(default_factory=dict)
    created_at: datetime


class RuntimeQueryLogCollectionResponse(BaseModel):
    query_logs: list[RuntimeQueryLogRecord]
    count: int


class AdminDashboardResponse(BaseModel):
    runtime_status: dict
    metrics: AdminMetricsSummary
    metadata_overview: MetadataOverview
    users: UserCollectionResponse
    roles: list[RoleRecord]
    query_logs: RuntimeQueryLogCollectionResponse
    feedback_summary: FeedbackSummary
    evaluation_summary: EvaluationSummary
    runtime_sessions: RuntimeSessionCollectionResponse


class RuntimeRiskSummaryResponse(BaseModel):
    total_queries: int
    by_risk_level: dict[str, int] = Field(default_factory=dict)
    by_risk_flag: dict[str, int] = Field(default_factory=dict)
    by_subject_domain: dict[str, int] = Field(default_factory=dict)


class RuntimeRetentionResponse(BaseModel):
    cutoff_iso: str
    deleted_rows: dict[str, int] = Field(default_factory=dict)


class RuntimeRetrievalLogRecord(BaseModel):
    retrieval_log_id: str
    trace_id: str
    rank_position: int
    source_type: str
    source_id: str
    summary: str | None = None
    retrieval_channel: str | None = None
    source_score: float | None = None
    score: float
    matched_features: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class RuntimeSqlAuditRecord(BaseModel):
    sql_audit_id: str
    trace_id: str
    sql_text: str | None = None
    context_valid: bool
    context_risk_level: str | None = None
    context_risk_flags: list[str] = Field(default_factory=list)
    sql_valid: bool
    sql_risk_level: str | None = None
    sql_risk_flags: list[str] = Field(default_factory=list)
    executed: bool
    row_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
