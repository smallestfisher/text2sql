from __future__ import annotations

import logging

from backend.app.models.auth import UserContext
from backend.app.models.workspace import SessionTraceWorkspaceRecord, SessionWorkspaceResponse


logger = logging.getLogger(__name__)


class SessionWorkspaceService:
    def __init__(
        self,
        *,
        session_service,
        runtime_log_repository,
        audit_service,
        response_restore_service,
        permission_service,
    ) -> None:
        self.session_service = session_service
        self.runtime_log_repository = runtime_log_repository
        self.audit_service = audit_service
        self.response_restore_service = response_restore_service
        self.permission_service = permission_service

    def get_workspace(
        self,
        session_id: str,
        *,
        user_context: UserContext | None = None,
    ) -> SessionWorkspaceResponse | None:
        session = self.session_service.get_session(session_id)
        if session is None:
            return None

        messages = self.session_service.history(session_id)
        state = self.permission_service.apply_to_session_state(
            self.session_service.resolve_state(session_id),
            user_context,
        )
        trace_ids = self._message_trace_ids(messages)
        query_logs = self._safe_list_query_logs(session_id, limit=max(len(trace_ids), 5))
        query_log_by_trace = {record.trace_id: record for record in query_logs}
        latest_query_logs = query_logs[:5]
        latest_trace_id = latest_query_logs[0].trace_id if latest_query_logs else (trace_ids[-1] if trace_ids else None)
        trace_artifacts = self._build_trace_artifacts(
            trace_ids=trace_ids,
            query_log_by_trace=query_log_by_trace,
            latest_trace_id=latest_trace_id,
            state=state,
            messages=messages,
            user_context=user_context,
        )
        latest_artifact = self._find_trace_artifact(trace_artifacts, latest_trace_id)

        return SessionWorkspaceResponse(
            session=self.permission_service.apply_to_chat_session(session, user_context),
            messages=messages,
            state=state,
            latest_response=latest_artifact.response if latest_artifact is not None else None,
            latest_trace=latest_artifact.trace if latest_artifact is not None else None,
            latest_sql_audit=latest_artifact.sql_audit if latest_artifact is not None else None,
            latest_query_logs=latest_query_logs,
            trace_artifacts=trace_artifacts,
        )

    def _build_trace_artifacts(
        self,
        *,
        trace_ids: list[str],
        query_log_by_trace: dict,
        latest_trace_id: str | None,
        state,
        messages,
        user_context,
    ) -> list[SessionTraceWorkspaceRecord]:
        artifacts: list[SessionTraceWorkspaceRecord] = []
        for trace_id in trace_ids:
            query_log = query_log_by_trace.get(trace_id) or self._safe_get_query_log(trace_id)
            trace = self._safe_get_trace(trace_id)
            sql_audit = self.permission_service.apply_to_sql_audit(
                self._safe_get_sql_audit(trace_id),
                user_context,
            )
            response = self._safe_restore_response(
                trace_id=trace_id,
                state=state if trace_id == latest_trace_id else None,
                messages=messages,
                user_context=user_context,
                trace=trace,
                query_log=query_log,
                sql_audit=sql_audit,
            )
            if query_log is None and trace is None and sql_audit is None and response is None:
                continue
            artifacts.append(
                SessionTraceWorkspaceRecord(
                    trace_id=trace_id,
                    response=response,
                    trace=trace,
                    sql_audit=sql_audit,
                    query_log=query_log,
                )
            )
        return artifacts

    def _safe_list_query_logs(self, session_id: str, limit: int):
        try:
            return self.runtime_log_repository.list_query_logs(limit=limit, session_id=session_id)
        except Exception:
            logger.exception("failed to load query logs for session_id=%s", session_id)
            return []

    def _safe_get_query_log(self, trace_id: str):
        try:
            return self.runtime_log_repository.get_query_log(trace_id)
        except Exception:
            logger.exception("failed to load query log trace_id=%s", trace_id)
            return None

    def _safe_get_trace(self, trace_id: str):
        try:
            return self.audit_service.get_trace(trace_id)
        except Exception:
            logger.exception("failed to load trace trace_id=%s", trace_id)
            return None

    def _safe_get_sql_audit(self, trace_id: str):
        try:
            return self.runtime_log_repository.get_sql_audit(trace_id)
        except Exception:
            logger.exception("failed to load sql audit trace_id=%s", trace_id)
            return None

    def _safe_restore_response(
        self,
        *,
        trace_id: str,
        state,
        messages,
        user_context,
        trace,
        query_log,
        sql_audit,
    ):
        try:
            return self.response_restore_service.build_from_trace_id(
                trace_id,
                session_state=state,
                messages=messages,
                user_context=user_context,
                trace=trace,
                query_log=query_log,
                sql_audit=sql_audit,
            )
        except Exception:
            logger.exception("failed to restore response snapshot trace_id=%s", trace_id)
            return None

    def _message_trace_ids(self, messages) -> list[str]:
        trace_ids: list[str] = []
        seen: set[str] = set()
        for message in messages:
            trace_id = getattr(message, "trace_id", None)
            if not trace_id or trace_id in seen:
                continue
            seen.add(trace_id)
            trace_ids.append(trace_id)
        return trace_ids

    def _find_trace_artifact(self, trace_artifacts: list[SessionTraceWorkspaceRecord], trace_id: str | None):
        if trace_id is None:
            return None
        for artifact in trace_artifacts:
            if artifact.trace_id == trace_id:
                return artifact
        return None
