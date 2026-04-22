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
    semantic_views: list[str]
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
    sql_valid: bool | None = None
    executed: bool | None = None
    row_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class RuntimeQueryLogCollectionResponse(BaseModel):
    query_logs: list[RuntimeQueryLogRecord]
    count: int


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
    sql_valid: bool
    executed: bool
    row_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
