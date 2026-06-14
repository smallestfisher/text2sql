from __future__ import annotations

from datetime import datetime
import json
import uuid

from backend.app.models.api import ExecutionResponse, ValidationResponse
from backend.app.models.admin import RuntimeQueryLogRecord, RuntimeRetrievalLogRecord, RuntimeSqlAuditRecord
from backend.app.models.retrieval import RetrievalContext
from backend.app.repositories.db_repository_utils import as_datetime, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbRuntimeLogRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def list_query_logs(
        self,
        limit: int = 50,
        offset: int = 0,
        session_id: str | None = None,
        user_id: str | None = None,
        sql_risk_level: str | None = None,
        subject_domain: str | None = None,
        risk_flag: str | None = None,
    ) -> list[RuntimeQueryLogRecord]:
        where_sql, params = self._query_log_filters(
            session_id=session_id,
            user_id=user_id,
            sql_risk_level=sql_risk_level,
            subject_domain=subject_domain,
            risk_flag=risk_flag,
        )
        params["limit"] = max(1, limit)
        params["offset"] = max(0, offset)
        rows = self.database_connector.fetch_all(
            f"""
            SELECT trace_id, session_id, user_id, question, question_type, subject_domain,
                   effective_question, context_relation, question_decision,
                   conversation_summary, semantic_brief, question_context_json,
                   answer_status, context_valid, context_risk_level, context_risk_flags_json,
                   sql_valid, sql_risk_level, sql_risk_flags_json,
                   executed, row_count, warnings_json, trace_json, created_at
            FROM query_logs
            {where_sql}
            ORDER BY created_at DESC, trace_id DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return [self._hydrate_query_log(row) for row in rows]

    def count_query_logs(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
        sql_risk_level: str | None = None,
        subject_domain: str | None = None,
        risk_flag: str | None = None,
    ) -> int:
        where_sql, params = self._query_log_filters(
            session_id=session_id,
            user_id=user_id,
            sql_risk_level=sql_risk_level,
            subject_domain=subject_domain,
            risk_flag=risk_flag,
        )
        row = self.database_connector.fetch_one(
            f"SELECT COUNT(*) AS total FROM query_logs {where_sql}",
            params,
        )
        return int(row["total"]) if row else 0

    def count_query_logs_created_between(self, start: datetime, end: datetime) -> int:
        row = self.database_connector.fetch_one(
            """
            SELECT COUNT(*) AS total
            FROM query_logs
            WHERE created_at >= :start AND created_at < :end
            """,
            {"start": start, "end": end},
        )
        return int(row["total"]) if row else 0

    def count_query_logs_created_summary(
        self,
        today_start: datetime,
        yesterday_start: datetime,
        tomorrow_start: datetime,
    ) -> dict[str, int]:
        row = self.database_connector.fetch_one(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN created_at >= :today_start AND created_at < :tomorrow_start THEN 1 ELSE 0 END) AS today,
                SUM(CASE WHEN created_at >= :yesterday_start AND created_at < :today_start THEN 1 ELSE 0 END) AS yesterday
            FROM query_logs
            """,
            {
                "today_start": today_start,
                "yesterday_start": yesterday_start,
                "tomorrow_start": tomorrow_start,
            },
        )
        return self._count_summary(row)

    @staticmethod
    def _query_log_filters(
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        sql_risk_level: str | None = None,
        subject_domain: str | None = None,
        risk_flag: str | None = None,
    ) -> tuple[str, dict[str, object]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if session_id:
            clauses.append("session_id = :session_id")
            params["session_id"] = session_id
        if user_id:
            clauses.append("user_id = :user_id")
            params["user_id"] = user_id
        if sql_risk_level:
            clauses.append("sql_risk_level = :sql_risk_level")
            params["sql_risk_level"] = sql_risk_level
        if subject_domain:
            clauses.append("subject_domain = :subject_domain")
            params["subject_domain"] = subject_domain
        if risk_flag:
            clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM query_risk_flags
                    WHERE query_risk_flags.trace_id = query_logs.trace_id
                      AND query_risk_flags.flag = :risk_flag
                )
                """
            )
            params["risk_flag"] = risk_flag
        return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def get_query_log(self, trace_id: str) -> RuntimeQueryLogRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT trace_id, session_id, user_id, question, question_type, subject_domain,
                   effective_question, context_relation, question_decision,
                   conversation_summary, semantic_brief, question_context_json,
                   answer_status, context_valid, context_risk_level, context_risk_flags_json,
                   sql_valid, sql_risk_level, sql_risk_flags_json,
                   executed, row_count, warnings_json, trace_json, created_at
            FROM query_logs
            WHERE trace_id = :trace_id
            """,
            {"trace_id": trace_id},
        )
        return None if row is None else self._hydrate_query_log(row)

    def get_query_logs_by_trace_ids(self, trace_ids: list[str]) -> dict[str, RuntimeQueryLogRecord]:
        if not trace_ids:
            return {}
        where_sql, params = self._trace_id_filter(trace_ids)
        rows = self.database_connector.fetch_all(
            f"""
            SELECT trace_id, session_id, user_id, question, question_type, subject_domain,
                   effective_question, context_relation, question_decision,
                   conversation_summary, semantic_brief, question_context_json,
                   answer_status, context_valid, context_risk_level, context_risk_flags_json,
                   sql_valid, sql_risk_level, sql_risk_flags_json,
                   executed, row_count, warnings_json, trace_json, created_at
            FROM query_logs
            WHERE trace_id IN ({where_sql})
            """,
            params,
        )
        return {record.trace_id: record for record in (self._hydrate_query_log(row) for row in rows)}

    def summarize_query_risks(self, limit: int = 200) -> dict:
        rows = self.database_connector.fetch_all(
            """
            SELECT subject_domain, context_risk_level, context_risk_flags_json, sql_risk_level, sql_risk_flags_json
            FROM query_logs
            ORDER BY created_at DESC, trace_id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        by_risk_level: dict[str, int] = {}
        by_risk_flag: dict[str, int] = {}
        by_subject_domain: dict[str, int] = {}
        for row in rows:
            risk_level = row.get("sql_risk_level") or row.get("context_risk_level") or "low"
            by_risk_level[risk_level] = by_risk_level.get(risk_level, 0) + 1
            subject_domain = row.get("subject_domain") or "unknown"
            by_subject_domain[subject_domain] = by_subject_domain.get(subject_domain, 0) + 1
            for flag in json_loads(row.get("context_risk_flags_json"), []) + json_loads(row.get("sql_risk_flags_json"), []):
                by_risk_flag[flag] = by_risk_flag.get(flag, 0) + 1
        return {
            "total_queries": len(rows),
            "by_risk_level": by_risk_level,
            "by_risk_flag": by_risk_flag,
            "by_subject_domain": by_subject_domain,
        }

    def purge_before(self, cutoff: datetime) -> dict[str, int]:
        deleted_rows: dict[str, int] = {}
        deletion_order = [
            ("retrieval_logs", "DELETE FROM retrieval_logs WHERE created_at < :cutoff"),
            ("sql_audit_logs", "DELETE FROM sql_audit_logs WHERE created_at < :cutoff"),
            ("query_risk_flags", "DELETE FROM query_risk_flags WHERE created_at < :cutoff"),
            ("feedback_logs", "DELETE FROM feedback_logs WHERE created_at < :cutoff"),
            ("session_state_snapshots", "DELETE FROM session_state_snapshots WHERE created_at < :cutoff"),
            ("chat_messages", "DELETE FROM chat_messages WHERE created_at < :cutoff"),
            ("query_logs", "DELETE FROM query_logs WHERE created_at < :cutoff"),
            ("evaluation_runs", "DELETE FROM evaluation_runs WHERE created_at < :cutoff"),
        ]
        for table_name, sql in deletion_order:
            deleted_rows[table_name] = self.database_connector.execute_write(sql, {"cutoff": cutoff})
        return deleted_rows

    def list_retrieval_logs(self, trace_id: str) -> list[RuntimeRetrievalLogRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT retrieval_log_id, trace_id, rank_position, source_type, source_id,
                   summary, retrieval_channel, source_score,
                   score, matched_features_json, metadata_json, created_at
            FROM retrieval_logs
            WHERE trace_id = :trace_id
            ORDER BY rank_position ASC, created_at ASC
            """,
            {"trace_id": trace_id},
        )
        records: list[RuntimeRetrievalLogRecord] = []
        for row in rows:
            records.append(
                RuntimeRetrievalLogRecord(
                    retrieval_log_id=row["retrieval_log_id"],
                    trace_id=row["trace_id"],
                    rank_position=int(row["rank_position"]),
                    source_type=row["source_type"],
                    source_id=row["source_id"],
                    summary=row.get("summary"),
                    retrieval_channel=row.get("retrieval_channel"),
                    source_score=(
                        float(row["source_score"])
                        if row.get("source_score") is not None
                        else None
                    ),
                    score=float(row["score"]),
                    matched_features=json_loads(row.get("matched_features_json"), []),
                    metadata=json_loads(row.get("metadata_json"), {}),
                    created_at=as_datetime(row["created_at"]),
                )
            )
        return records

    def get_sql_audit(self, trace_id: str) -> RuntimeSqlAuditRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT sql_audit_id, trace_id, sql_text, context_valid, context_risk_level, context_risk_flags_json,
                   sql_valid,
                   sql_risk_level, sql_risk_flags_json, executed,
                   row_count, warnings_json, errors_json, created_at
            FROM sql_audit_logs
            WHERE trace_id = :trace_id
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"trace_id": trace_id},
        )
        if row is None:
            return None
        return self._hydrate_sql_audit(row)

    def get_sql_audits_by_trace_ids(self, trace_ids: list[str]) -> dict[str, RuntimeSqlAuditRecord]:
        if not trace_ids:
            return {}
        where_sql, params = self._trace_id_filter(trace_ids)
        rows = self.database_connector.fetch_all(
            f"""
            SELECT sql_audit_id, trace_id, sql_text, context_valid, context_risk_level, context_risk_flags_json,
                   sql_valid,
                   sql_risk_level, sql_risk_flags_json, executed,
                   row_count, warnings_json, errors_json, created_at
            FROM sql_audit_logs
            WHERE trace_id IN ({where_sql})
            ORDER BY created_at DESC
            """,
            params,
        )
        audits: dict[str, RuntimeSqlAuditRecord] = {}
        for row in rows:
            trace_id = row["trace_id"]
            if trace_id not in audits:
                audits[trace_id] = self._hydrate_sql_audit(row)
        return audits

    def _hydrate_sql_audit(self, row: dict) -> RuntimeSqlAuditRecord:
        return RuntimeSqlAuditRecord(
            sql_audit_id=row["sql_audit_id"],
            trace_id=row["trace_id"],
            sql_text=row["sql_text"],
            context_valid=bool(row["context_valid"]),
            context_risk_level=row.get("context_risk_level"),
            context_risk_flags=json_loads(row.get("context_risk_flags_json"), []),
            sql_valid=bool(row["sql_valid"]),
            sql_risk_level=row.get("sql_risk_level"),
            sql_risk_flags=json_loads(row.get("sql_risk_flags_json"), []),
            executed=bool(row["executed"]),
            row_count=row["row_count"],
            warnings=json_loads(row.get("warnings_json"), []),
            errors=json_loads(row.get("errors_json"), []),
            created_at=as_datetime(row["created_at"]),
        )

    def log_query(
        self,
        *,
        trace_id: str,
        session_id: str | None,
        user_id: str | None,
        question: str,
        question_type: str | None,
        subject_domain: str | None,
        answer_status: str | None,
        context_validation: ValidationResponse,
        sql_validation: ValidationResponse,
        execution: ExecutionResponse | None,
        warnings: list[str],
    ) -> None:
        self.database_connector.execute_write(
            """
            UPDATE query_logs
            SET
                session_id = :session_id,
                user_id = :user_id,
                question = :question,
                question_type = :question_type,
                subject_domain = :subject_domain,
                answer_status = :answer_status,
                context_valid = :context_valid,
                context_risk_level = :context_risk_level,
                context_risk_flags_json = :context_risk_flags_json,
                sql_valid = :sql_valid,
                sql_risk_level = :sql_risk_level,
                sql_risk_flags_json = :sql_risk_flags_json,
                executed = :executed,
                row_count = :row_count,
                warnings_json = :warnings_json
            WHERE trace_id = :trace_id
            """,
            {
                "trace_id": trace_id,
                "session_id": session_id,
                "user_id": user_id,
                "question": question,
                "question_type": question_type,
                "subject_domain": subject_domain,
                "answer_status": answer_status,
                "context_valid": context_validation.valid,
                "context_risk_level": context_validation.risk_level,
                "context_risk_flags_json": json.dumps(context_validation.risk_flags, ensure_ascii=False),
                "sql_valid": sql_validation.valid,
                "sql_risk_level": sql_validation.risk_level,
                "sql_risk_flags_json": json.dumps(sql_validation.risk_flags, ensure_ascii=False),
                "executed": bool(execution and execution.executed),
                "row_count": execution.row_count if execution is not None else None,
                "warnings_json": json.dumps(
                    warnings
                    + ([f"execution_status:{execution.status}"] if execution is not None else [])
                    + ([f"execution_error_category:{execution.error_category}"] if execution and execution.error_category else []),
                    ensure_ascii=False,
                ),
            },
        )
        self._replace_query_risk_flags(
            trace_id=trace_id,
            context_flags=context_validation.risk_flags,
            sql_flags=sql_validation.risk_flags,
            created_at=datetime.utcnow(),
        )

    def log_retrieval(self, trace_id: str, retrieval: RetrievalContext) -> None:
        self.database_connector.execute_write(
            "DELETE FROM retrieval_logs WHERE trace_id = :trace_id",
            {"trace_id": trace_id},
        )
        now = datetime.utcnow()
        for index, hit in enumerate(retrieval.hits, start=1):
            self.database_connector.execute_write(
                """
                INSERT INTO retrieval_logs (
                    retrieval_log_id, trace_id, rank_position, source_type, source_id,
                    summary, retrieval_channel, source_score,
                    score, matched_features_json, metadata_json, created_at
                ) VALUES (
                    :retrieval_log_id, :trace_id, :rank_position, :source_type, :source_id,
                    :summary, :retrieval_channel, :source_score,
                    :score, :matched_features_json, :metadata_json, :created_at
                )
                """,
                {
                    "retrieval_log_id": f"rl_{uuid.uuid4().hex[:16]}",
                    "trace_id": trace_id,
                    "rank_position": index,
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "summary": hit.summary,
                    "retrieval_channel": hit.retrieval_channel,
                    "source_score": hit.source_score,
                    "score": hit.score,
                    "matched_features_json": json.dumps(hit.matched_features, ensure_ascii=False),
                    "metadata_json": json.dumps(hit.metadata, ensure_ascii=False),
                    "created_at": now,
                },
            )

    def log_sql_audit(
        self,
        *,
        trace_id: str,
        sql: str | None,
        context_validation: ValidationResponse,
        sql_validation: ValidationResponse,
        execution: ExecutionResponse | None,
    ) -> None:
        self.database_connector.execute_write(
            "DELETE FROM sql_audit_logs WHERE trace_id = :trace_id",
            {"trace_id": trace_id},
        )
        self.database_connector.execute_write(
            """
            INSERT INTO sql_audit_logs (
                sql_audit_id, trace_id, sql_text, context_valid, context_risk_level, context_risk_flags_json,
                sql_valid, executed, sql_risk_level, sql_risk_flags_json, row_count, warnings_json, errors_json, created_at
            ) VALUES (
                :sql_audit_id, :trace_id, :sql_text, :context_valid, :context_risk_level, :context_risk_flags_json,
                :sql_valid, :executed, :sql_risk_level, :sql_risk_flags_json, :row_count, :warnings_json, :errors_json, :created_at
            )
            """,
            {
                "sql_audit_id": f"sa_{uuid.uuid4().hex[:16]}",
                "trace_id": trace_id,
                "sql_text": sql,
                "context_valid": context_validation.valid,
                "context_risk_level": context_validation.risk_level,
                "context_risk_flags_json": json.dumps(context_validation.risk_flags, ensure_ascii=False),
                "sql_valid": sql_validation.valid,
                "sql_risk_level": sql_validation.risk_level,
                "sql_risk_flags_json": json.dumps(sql_validation.risk_flags, ensure_ascii=False),
                "executed": bool(execution and execution.executed),
                "row_count": execution.row_count if execution is not None else None,
                "warnings_json": json.dumps(
                    sql_validation.warnings + (execution.warnings if execution is not None else []),
                    ensure_ascii=False,
                ),
                "errors_json": json.dumps(context_validation.errors + sql_validation.errors, ensure_ascii=False),
                "created_at": datetime.utcnow(),
            },
        )

    def _replace_query_risk_flags(
        self,
        *,
        trace_id: str,
        context_flags: list[str],
        sql_flags: list[str],
        created_at: datetime,
    ) -> None:
        self.database_connector.execute_write(
            "DELETE FROM query_risk_flags WHERE trace_id = :trace_id",
            {"trace_id": trace_id},
        )
        for source, flags in (("context", context_flags), ("sql", sql_flags)):
            for flag in dict.fromkeys(item for item in flags if item):
                self.database_connector.execute_write(
                    """
                    INSERT INTO query_risk_flags (trace_id, source, flag, created_at)
                    VALUES (:trace_id, :source, :flag, :created_at)
                    """,
                    {
                        "trace_id": trace_id,
                        "source": source,
                        "flag": flag,
                        "created_at": created_at,
                    },
                )

    def _hydrate_query_log(self, row: dict) -> RuntimeQueryLogRecord:
        trace_payload = json_loads(row.get("trace_json"), {})
        question_context = json_loads(row.get("question_context_json"), {})
        if not question_context:
            question_context = self._extract_question_context(trace_payload)
        return RuntimeQueryLogRecord(
            trace_id=row["trace_id"],
            session_id=row.get("session_id"),
            user_id=row.get("user_id"),
            question=row.get("question"),
            effective_question=row.get("effective_question")
            or question_context.get("effective_question"),
            context_relation=row.get("context_relation")
            or question_context.get("context_relation"),
            question_decision=row.get("question_decision")
            or question_context.get("decision"),
            conversation_summary=row.get("conversation_summary")
            or question_context.get("conversation_summary"),
            semantic_brief=row.get("semantic_brief")
            or question_context.get("semantic_brief"),
            question_context=question_context,
            question_type=row.get("question_type"),
            subject_domain=row.get("subject_domain"),
            answer_status=row.get("answer_status"),
            context_valid=bool(row["context_valid"]) if row.get("context_valid") is not None else None,
            context_risk_level=row.get("context_risk_level"),
            context_risk_flags=json_loads(row.get("context_risk_flags_json"), []),
            sql_valid=bool(row["sql_valid"]) if row.get("sql_valid") is not None else None,
            sql_risk_level=row.get("sql_risk_level"),
            sql_risk_flags=json_loads(row.get("sql_risk_flags_json"), []),
            executed=bool(row["executed"]) if row.get("executed") is not None else None,
            row_count=row.get("row_count"),
            total_elapsed_ms=self._extract_total_elapsed_ms(trace_payload),
            warnings=json_loads(row.get("warnings_json"), []),
            prompt_context_summary=self._extract_prompt_context_summary(trace_payload),
            created_at=as_datetime(row["created_at"]),
        )

    def _extract_prompt_context_summary(self, trace_payload: dict) -> dict:
        for step in trace_payload.get("steps", []):
            if step.get("name") != "build_sql_prompt":
                continue
            metadata = step.get("metadata") or {}
            summary = metadata.get("context_summary")
            if isinstance(summary, dict):
                return summary
        return {}

    def _extract_question_context(self, trace_payload: dict) -> dict:
        for step in trace_payload.get("steps", []):
            if step.get("name") != "question_context":
                continue
            metadata = step.get("metadata") or {}
            question_context = metadata.get("question_context")
            if isinstance(question_context, dict):
                return question_context
        return {}

    def _extract_total_elapsed_ms(self, trace_payload: dict) -> int | None:
        for step in trace_payload.get("steps", []):
            if step.get("name") != "chat_total":
                continue
            metadata = step.get("metadata") or {}
            elapsed_ms = metadata.get("elapsed_ms")
            if isinstance(elapsed_ms, (int, float)):
                return int(elapsed_ms)
        return None

    @staticmethod
    def _trace_id_filter(trace_ids: list[str]) -> tuple[str, dict[str, object]]:
        params: dict[str, object] = {}
        placeholders: list[str] = []
        for index, trace_id in enumerate(dict.fromkeys(trace_ids)):
            key = f"trace_id_{index}"
            placeholders.append(f":{key}")
            params[key] = trace_id
        return ", ".join(placeholders), params

    @staticmethod
    def _count_summary(row: dict | None) -> dict[str, int]:
        if row is None:
            return {"total": 0, "today": 0, "yesterday": 0}
        return {
            "total": int(row.get("total") or 0),
            "today": int(row.get("today") or 0),
            "yesterday": int(row.get("yesterday") or 0),
        }
