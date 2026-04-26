from __future__ import annotations

from pydantic import BaseModel, Field

from .admin import RuntimeQueryLogRecord, RuntimeSqlAuditRecord
from .api import ChatResponse
from .conversation import ChatMessage, ChatSession
from .session_state import SessionState
from .trace import TraceRecord


class SessionTraceWorkspaceRecord(BaseModel):
    trace_id: str
    response: ChatResponse | None = None
    trace: TraceRecord | None = None
    sql_audit: RuntimeSqlAuditRecord | None = None
    query_log: RuntimeQueryLogRecord | None = None


class SessionWorkspaceResponse(BaseModel):
    session: ChatSession
    messages: list[ChatMessage]
    state: SessionState | None = None
    latest_response: ChatResponse | None = None
    latest_trace: TraceRecord | None = None
    latest_sql_audit: RuntimeSqlAuditRecord | None = None
    latest_query_logs: list[RuntimeQueryLogRecord] = Field(default_factory=list)
    trace_artifacts: list[SessionTraceWorkspaceRecord] = Field(default_factory=list)
