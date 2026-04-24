from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.models.admin import (
    RuntimeQueryLogCollectionResponse,
    RuntimeQueryLogRecord,
    RuntimeRetrievalLogRecord,
    RuntimeRetentionResponse,
    RuntimeRiskSummaryResponse,
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
        sql_risk_level: str | None = None,
        subject_domain: str | None = None,
        risk_flag: str | None = None,
    ) -> RuntimeQueryLogCollectionResponse:
        query_logs = self.runtime_log_repository.list_query_logs(
            limit=limit,
            session_id=session_id,
            user_id=user_id,
            sql_risk_level=sql_risk_level,
            subject_domain=subject_domain,
            risk_flag=risk_flag,
        )
        return RuntimeQueryLogCollectionResponse(query_logs=query_logs, count=len(query_logs))

    def get_query_log(self, trace_id: str) -> RuntimeQueryLogRecord | None:
        return self.runtime_log_repository.get_query_log(trace_id)

    def summarize_query_risks(self, limit: int = 200) -> RuntimeRiskSummaryResponse:
        summary = self.runtime_log_repository.summarize_query_risks(limit=limit)
        return RuntimeRiskSummaryResponse(**summary)

    def purge_runtime_data(self, retention_days: int) -> RuntimeRetentionResponse:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
        deleted_rows = self.runtime_log_repository.purge_before(cutoff=cutoff)
        return RuntimeRetentionResponse(
            cutoff_iso=cutoff.isoformat(),
            deleted_rows=deleted_rows,
        )

    def list_retrieval_logs(self, trace_id: str) -> list[RuntimeRetrievalLogRecord]:
        return self.runtime_log_repository.list_retrieval_logs(trace_id)

    def get_sql_audit(self, trace_id: str) -> RuntimeSqlAuditRecord | None:
        return self.runtime_log_repository.get_sql_audit(trace_id)
