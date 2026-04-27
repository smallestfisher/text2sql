from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field

from .conversation import ChatSession
from .example_library import ExampleRecord
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


class ExampleCollectionResponse(BaseModel):
    examples: list[ExampleRecord]
    count: int


class ExampleMutationResponse(BaseModel):
    created: bool | None = None
    updated: bool | None = None
    example: ExampleRecord
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
    question_type: str | None = None
    subject_domain: str | None = None
    answer_status: str | None = None
    plan_valid: bool | None = None
    plan_risk_level: str | None = None
    plan_risk_flags: list[str] = Field(default_factory=list)
    sql_valid: bool | None = None
    sql_risk_level: str | None = None
    sql_risk_flags: list[str] = Field(default_factory=list)
    executed: bool | None = None
    row_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    prompt_context_summary: dict = Field(default_factory=dict)
    created_at: datetime


class RuntimeQueryLogCollectionResponse(BaseModel):
    query_logs: list[RuntimeQueryLogRecord]
    count: int


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
    score: float
    matched_features: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class RuntimeSqlAuditRecord(BaseModel):
    sql_audit_id: str
    trace_id: str
    sql_text: str | None = None
    plan_valid: bool
    plan_risk_level: str | None = None
    plan_risk_flags: list[str] = Field(default_factory=list)
    sql_valid: bool
    sql_risk_level: str | None = None
    sql_risk_flags: list[str] = Field(default_factory=list)
    executed: bool
    row_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
