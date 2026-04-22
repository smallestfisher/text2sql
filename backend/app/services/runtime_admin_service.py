from __future__ import annotations

from backend.app.models.admin import (
    RuntimeQueryLogCollectionResponse,
    RuntimeQueryLogRecord,
    RuntimeRetrievalLogRecord,
    RuntimeSessionCollectionResponse,
    RuntimeSqlAuditRecord,
    SessionSnapshotRecord,
)
from backend.app.models.conversation import SessionHistoryResponse


class RuntimeAdminService:
    def __init__(self, session_repository, runtime_log_repository) -> None:
        self.session_repository = session_repository
        self.runtime_log_repository = runtime_log_repository

    def list_sessions(self, limit: int = 50) -> RuntimeSessionCollectionResponse:
        sessions = self.session_repository.list_sessions(limit=limit)
        return RuntimeSessionCollectionResponse(sessions=sessions, count=len(sessions))

    def get_session_history(self, session_id: str) -> SessionHistoryResponse | None:
        session = self.session_repository.get_session(session_id)
        if session is None:
            return None
        messages = self.session_repository.list_messages(session_id)
        return SessionHistoryResponse(session=session, messages=messages)

    def list_session_snapshots(self, session_id: str, limit: int = 50) -> list[SessionSnapshotRecord]:
        return self.session_repository.list_state_snapshots(session_id=session_id, limit=limit)

    def list_query_logs(
        self,
        limit: int = 50,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> RuntimeQueryLogCollectionResponse:
        query_logs = self.runtime_log_repository.list_query_logs(
            limit=limit,
            session_id=session_id,
            user_id=user_id,
        )
        return RuntimeQueryLogCollectionResponse(query_logs=query_logs, count=len(query_logs))

    def get_query_log(self, trace_id: str) -> RuntimeQueryLogRecord | None:
        return self.runtime_log_repository.get_query_log(trace_id)

    def list_retrieval_logs(self, trace_id: str) -> list[RuntimeRetrievalLogRecord]:
        return self.runtime_log_repository.list_retrieval_logs(trace_id)

    def get_sql_audit(self, trace_id: str) -> RuntimeSqlAuditRecord | None:
        return self.runtime_log_repository.get_sql_audit(trace_id)
