from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import text

from backend.app.models.api import ChatResponse, PlanRequest, ValidationResponse
from backend.app.models.trace import TraceRecord
from backend.app.repositories.db_repository_utils import json_dumps
from backend.app.services.database_connector import DatabaseConnector


class ConversationPersistenceService:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def persist_success(
        self,
        *,
        trace: TraceRecord,
        request: PlanRequest,
        response: ChatResponse,
        warnings: list[str],
    ) -> None:
        if warnings:
            trace.warnings.extend(warnings)
        with self.database_connector.begin() as connection:
            if request.session_id:
                self._persist_session_exchange(connection, request=request, response=response, trace=trace)
            self._upsert_query_log(
                connection,
                trace=trace,
                request=request,
                response=response,
                warnings=warnings,
            )
            self._replace_retrieval_logs(connection, trace_id=trace.trace_id, retrieval=response.retrieval)
            self._replace_sql_audit(
                connection,
                trace_id=trace.trace_id,
                sql=response.sql,
                plan_validation=response.plan_validation,
                sql_validation=response.sql_validation,
                execution=response.execution,
            )

    def persist_failure(
        self,
        *,
        trace: TraceRecord,
        request: PlanRequest,
        warnings: list[str],
        answer_status: str,
        classification=None,
        retrieval=None,
        plan_validation: ValidationResponse | None = None,
        sql_validation: ValidationResponse | None = None,
        execution=None,
        sql: str | None = None,
    ) -> None:
        if warnings:
            trace.warnings.extend(warnings)
        with self.database_connector.begin() as connection:
            self._upsert_query_log(
                connection,
                trace=trace,
                request=request,
                response=None,
                warnings=warnings,
                answer_status=answer_status,
                classification=classification,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
            )
            self._replace_retrieval_logs(connection, trace_id=trace.trace_id, retrieval=retrieval)
            if plan_validation is not None and sql_validation is not None:
                self._replace_sql_audit(
                    connection,
                    trace_id=trace.trace_id,
                    sql=sql,
                    plan_validation=plan_validation,
                    sql_validation=sql_validation,
                    execution=execution,
                )

    def _persist_session_exchange(self, connection, *, request: PlanRequest, response: ChatResponse, trace: TraceRecord) -> None:
        session_id = request.session_id
        if session_id is None:
            return
        now = datetime.now(timezone.utc)
        session_row = connection.execute(
            text(
                """
                SELECT title
                FROM chat_sessions
                WHERE session_id = :session_id
                FOR UPDATE
                """
            ),
            {"session_id": session_id},
        ).mappings().first()
        last_message_row = connection.execute(
            text(
                """
                SELECT created_at
                FROM chat_messages
                WHERE session_id = :session_id
                ORDER BY created_at DESC, message_id DESC
                LIMIT 1
                """
            ),
            {"session_id": session_id},
        ).mappings().first()
        base_created_at = now
        if last_message_row is not None and last_message_row.get("created_at") is not None:
            candidate = last_message_row["created_at"]
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=timezone.utc)
            candidate = candidate + timedelta(seconds=1)
            if candidate > base_created_at:
                base_created_at = candidate
        assistant_created_at = base_created_at + timedelta(seconds=1)
        connection.execute(
            text(
                """
                INSERT INTO chat_messages (message_id, session_id, role, content, trace_id, created_at)
                VALUES (:message_id, :session_id, :role, :content, :trace_id, :created_at)
                """
            ),
            {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id,
                "role": "user",
                "content": request.question,
                "trace_id": trace.trace_id,
                "created_at": base_created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO chat_messages (message_id, session_id, role, content, trace_id, created_at)
                VALUES (:message_id, :session_id, :role, :content, :trace_id, :created_at)
                """
            ),
            {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id,
                "role": "assistant",
                "content": response.answer.summary if response.answer is not None else "",
                "trace_id": trace.trace_id,
                "created_at": assistant_created_at,
            },
        )
        title = request.question[:40]
        next_session_state = response.next_session_state.model_copy(deep=True)
        next_session_state.session_id = session_id
        state_json = json_dumps(next_session_state.model_dump(mode="json"))
        connection.execute(
            text(
                """
                UPDATE chat_sessions
                SET
                    title = CASE
                        WHEN title IS NULL OR title = '' THEN :title
                        ELSE title
                    END,
                    current_state_json = :current_state_json,
                    updated_at = :updated_at
                WHERE session_id = :session_id
                """
            ),
            {
                "session_id": session_id,
                "title": title,
                "current_state_json": state_json,
                "updated_at": assistant_created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO session_state_snapshots (snapshot_id, session_id, trace_id, state_json, created_at)
                VALUES (:snapshot_id, :session_id, :trace_id, :state_json, :created_at)
                """
            ),
            {
                "snapshot_id": f"ss_{uuid.uuid4().hex[:16]}",
                "session_id": session_id,
                "trace_id": trace.trace_id,
                "state_json": state_json,
                "created_at": assistant_created_at,
            },
        )
        if session_row is None:
            raise RuntimeError(f"session not found during persistence: {session_id}")

    def _upsert_query_log(
        self,
        connection,
        *,
        trace: TraceRecord,
        request: PlanRequest,
        warnings: list[str],
        response: ChatResponse | None,
        answer_status: str | None = None,
        classification=None,
        plan_validation: ValidationResponse | None = None,
        sql_validation: ValidationResponse | None = None,
        execution=None,
    ) -> None:
        final_classification = response.classification if response is not None else classification
        final_plan_validation = response.plan_validation if response is not None else plan_validation
        final_sql_validation = response.sql_validation if response is not None else sql_validation
        final_execution = response.execution if response is not None else execution
        final_answer_status = (
            response.answer.status
            if response is not None and response.answer is not None
            else answer_status
        )
        persisted_warnings = list(warnings)
        if final_execution is not None:
            persisted_warnings.append(f"execution_status:{final_execution.status}")
            if final_execution.error_category:
                persisted_warnings.append(f"execution_error_category:{final_execution.error_category}")
        params = {
            "trace_id": trace.trace_id,
            "session_id": request.session_id,
            "user_id": request.user_context.user_id if request.user_context else None,
            "question": request.question,
            "question_type": final_classification.question_type if final_classification is not None else None,
            "subject_domain": final_classification.subject_domain if final_classification is not None else None,
            "answer_status": final_answer_status,
            "plan_valid": final_plan_validation.valid if final_plan_validation is not None else None,
            "plan_risk_level": final_plan_validation.risk_level if final_plan_validation is not None else None,
            "plan_risk_flags_json": json_dumps(final_plan_validation.risk_flags) if final_plan_validation is not None else None,
            "sql_valid": final_sql_validation.valid if final_sql_validation is not None else None,
            "sql_risk_level": final_sql_validation.risk_level if final_sql_validation is not None else None,
            "sql_risk_flags_json": json_dumps(final_sql_validation.risk_flags) if final_sql_validation is not None else None,
            "executed": bool(final_execution and final_execution.executed) if final_execution is not None else None,
            "row_count": final_execution.row_count if final_execution is not None else None,
            "warnings_json": json_dumps(persisted_warnings),
            "trace_json": json_dumps(trace.model_dump(mode="json")),
            "created_at": trace.created_at,
        }
        updated = connection.execute(
            text(
                """
                UPDATE query_logs
                SET session_id = :session_id,
                    user_id = :user_id,
                    question = :question,
                    question_type = :question_type,
                    subject_domain = :subject_domain,
                    answer_status = :answer_status,
                    plan_valid = :plan_valid,
                    plan_risk_level = :plan_risk_level,
                    plan_risk_flags_json = :plan_risk_flags_json,
                    sql_valid = :sql_valid,
                    sql_risk_level = :sql_risk_level,
                    sql_risk_flags_json = :sql_risk_flags_json,
                    executed = :executed,
                    row_count = :row_count,
                    warnings_json = :warnings_json,
                    trace_json = :trace_json
                WHERE trace_id = :trace_id
                """
            ),
            params,
        )
        if int(updated.rowcount or 0) > 0:
            return
        connection.execute(
            text(
                """
                INSERT INTO query_logs (
                    trace_id, session_id, user_id, question, question_type, subject_domain,
                    answer_status, plan_valid, plan_risk_level, plan_risk_flags_json,
                    sql_valid, sql_risk_level, sql_risk_flags_json,
                    executed, row_count, warnings_json, trace_json, created_at
                ) VALUES (
                    :trace_id, :session_id, :user_id, :question, :question_type, :subject_domain,
                    :answer_status, :plan_valid, :plan_risk_level, :plan_risk_flags_json,
                    :sql_valid, :sql_risk_level, :sql_risk_flags_json,
                    :executed, :row_count, :warnings_json, :trace_json, :created_at
                )
                """
            ),
            params,
        )

    def _replace_retrieval_logs(self, connection, *, trace_id: str, retrieval) -> None:
        connection.execute(
            text("DELETE FROM retrieval_logs WHERE trace_id = :trace_id"),
            {"trace_id": trace_id},
        )
        if retrieval is None:
            return
        now = datetime.utcnow()
        for index, hit in enumerate(retrieval.hits, start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO retrieval_logs (
                        retrieval_log_id, trace_id, rank_position, source_type, source_id,
                        score, matched_features_json, metadata_json, created_at
                    ) VALUES (
                        :retrieval_log_id, :trace_id, :rank_position, :source_type, :source_id,
                        :score, :matched_features_json, :metadata_json, :created_at
                    )
                    """
                ),
                {
                    "retrieval_log_id": f"rl_{uuid.uuid4().hex[:16]}",
                    "trace_id": trace_id,
                    "rank_position": index,
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "score": hit.score,
                    "matched_features_json": json_dumps(hit.matched_features),
                    "metadata_json": json_dumps(hit.metadata),
                    "created_at": now,
                },
            )

    def _replace_sql_audit(
        self,
        connection,
        *,
        trace_id: str,
        sql: str | None,
        plan_validation: ValidationResponse,
        sql_validation: ValidationResponse,
        execution,
    ) -> None:
        connection.execute(
            text("DELETE FROM sql_audit_logs WHERE trace_id = :trace_id"),
            {"trace_id": trace_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO sql_audit_logs (
                    sql_audit_id, trace_id, sql_text, plan_valid, plan_risk_level, plan_risk_flags_json,
                    sql_valid, executed, sql_risk_level, sql_risk_flags_json, row_count, warnings_json, errors_json, created_at
                ) VALUES (
                    :sql_audit_id, :trace_id, :sql_text, :plan_valid, :plan_risk_level, :plan_risk_flags_json,
                    :sql_valid, :executed, :sql_risk_level, :sql_risk_flags_json, :row_count, :warnings_json, :errors_json, :created_at
                )
                """
            ),
            {
                "sql_audit_id": f"sa_{uuid.uuid4().hex[:16]}",
                "trace_id": trace_id,
                "sql_text": sql,
                "plan_valid": plan_validation.valid,
                "plan_risk_level": plan_validation.risk_level,
                "plan_risk_flags_json": json_dumps(plan_validation.risk_flags),
                "sql_valid": sql_validation.valid,
                "sql_risk_level": sql_validation.risk_level,
                "sql_risk_flags_json": json_dumps(sql_validation.risk_flags),
                "executed": bool(execution and execution.executed),
                "row_count": execution.row_count if execution is not None else None,
                "warnings_json": json_dumps(
                    sql_validation.warnings + (execution.warnings if execution is not None else [])
                ),
                "errors_json": json_dumps(plan_validation.errors + sql_validation.errors),
                "created_at": datetime.utcnow(),
            },
        )
