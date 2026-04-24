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
        session_id: str | None = None,
        user_id: str | None = None,
        sql_risk_level: str | None = None,
        subject_domain: str | None = None,
        risk_flag: str | None = None,
    ) -> list[RuntimeQueryLogRecord]:
        clauses: list[str] = []
        params: dict[str, object] = {"limit": max(limit * 3, limit)}
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
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.database_connector.fetch_all(
            f"""
            SELECT trace_id, session_id, user_id, question, question_type, subject_domain,
                   answer_status, plan_valid, plan_risk_level, plan_risk_flags_json,
                   sql_valid, sql_risk_level, sql_risk_flags_json,
                   executed, row_count, warnings_json, created_at
            FROM query_logs
            {where_sql}
            ORDER BY created_at DESC, trace_id DESC
            LIMIT :limit
            """,
            params,
        )
        records = [self._hydrate_query_log(row) for row in rows]
        if risk_flag:
            records = [
                record
                for record in records
                if risk_flag in record.plan_risk_flags or risk_flag in record.sql_risk_flags
            ]
        return records[:limit]

    def get_query_log(self, trace_id: str) -> RuntimeQueryLogRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT trace_id, session_id, user_id, question, question_type, subject_domain,
                   answer_status, plan_valid, plan_risk_level, plan_risk_flags_json,
                   sql_valid, sql_risk_level, sql_risk_flags_json,
                   executed, row_count, warnings_json, created_at
            FROM query_logs
            WHERE trace_id = :trace_id
            """,
            {"trace_id": trace_id},
        )
        return None if row is None else self._hydrate_query_log(row)

    def summarize_query_risks(self, limit: int = 200) -> dict:
        rows = self.database_connector.fetch_all(
            """
            SELECT subject_domain, plan_risk_level, plan_risk_flags_json, sql_risk_level, sql_risk_flags_json
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
            risk_level = row.get("sql_risk_level") or row.get("plan_risk_level") or "low"
            by_risk_level[risk_level] = by_risk_level.get(risk_level, 0) + 1
            subject_domain = row.get("subject_domain") or "unknown"
            by_subject_domain[subject_domain] = by_subject_domain.get(subject_domain, 0) + 1
            for flag in json_loads(row.get("plan_risk_flags_json"), []) + json_loads(row.get("sql_risk_flags_json"), []):
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
            SELECT sql_audit_id, trace_id, sql_text, plan_valid, plan_risk_level, plan_risk_flags_json,
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
        return RuntimeSqlAuditRecord(
            sql_audit_id=row["sql_audit_id"],
            trace_id=row["trace_id"],
            sql_text=row["sql_text"],
            plan_valid=bool(row["plan_valid"]),
            plan_risk_level=row.get("plan_risk_level"),
            plan_risk_flags=json_loads(row.get("plan_risk_flags_json"), []),
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
        plan_validation: ValidationResponse,
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
                plan_valid = :plan_valid,
                plan_risk_level = :plan_risk_level,
                plan_risk_flags_json = :plan_risk_flags_json,
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
                "plan_valid": plan_validation.valid,
                "plan_risk_level": plan_validation.risk_level,
                "plan_risk_flags_json": json.dumps(plan_validation.risk_flags, ensure_ascii=False),
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
                    score, matched_features_json, metadata_json, created_at
                ) VALUES (
                    :retrieval_log_id, :trace_id, :rank_position, :source_type, :source_id,
                    :score, :matched_features_json, :metadata_json, :created_at
                )
                """,
                {
                    "retrieval_log_id": f"rl_{uuid.uuid4().hex[:16]}",
                    "trace_id": trace_id,
                    "rank_position": index,
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
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
        plan_validation: ValidationResponse,
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
                sql_audit_id, trace_id, sql_text, plan_valid, plan_risk_level, plan_risk_flags_json,
                sql_valid, executed, sql_risk_level, sql_risk_flags_json, row_count, warnings_json, errors_json, created_at
            ) VALUES (
                :sql_audit_id, :trace_id, :sql_text, :plan_valid, :plan_risk_level, :plan_risk_flags_json,
                :sql_valid, :executed, :sql_risk_level, :sql_risk_flags_json, :row_count, :warnings_json, :errors_json, :created_at
            )
            """,
            {
                "sql_audit_id": f"sa_{uuid.uuid4().hex[:16]}",
                "trace_id": trace_id,
                "sql_text": sql,
                "plan_valid": plan_validation.valid,
                "plan_risk_level": plan_validation.risk_level,
                "plan_risk_flags_json": json.dumps(plan_validation.risk_flags, ensure_ascii=False),
                "sql_valid": sql_validation.valid,
                "sql_risk_level": sql_validation.risk_level,
                "sql_risk_flags_json": json.dumps(sql_validation.risk_flags, ensure_ascii=False),
                "executed": bool(execution and execution.executed),
                "row_count": execution.row_count if execution is not None else None,
                "warnings_json": json.dumps(
                    sql_validation.warnings + (execution.warnings if execution is not None else []),
                    ensure_ascii=False,
                ),
                "errors_json": json.dumps(plan_validation.errors + sql_validation.errors, ensure_ascii=False),
                "created_at": datetime.utcnow(),
            },
        )

    def _hydrate_query_log(self, row: dict) -> RuntimeQueryLogRecord:
        return RuntimeQueryLogRecord(
            trace_id=row["trace_id"],
            session_id=row.get("session_id"),
            user_id=row.get("user_id"),
            question=row.get("question"),
            question_type=row.get("question_type"),
            subject_domain=row.get("subject_domain"),
            answer_status=row.get("answer_status"),
            plan_valid=bool(row["plan_valid"]) if row.get("plan_valid") is not None else None,
            plan_risk_level=row.get("plan_risk_level"),
            plan_risk_flags=json_loads(row.get("plan_risk_flags_json"), []),
            sql_valid=bool(row["sql_valid"]) if row.get("sql_valid") is not None else None,
            sql_risk_level=row.get("sql_risk_level"),
            sql_risk_flags=json_loads(row.get("sql_risk_flags_json"), []),
            executed=bool(row["executed"]) if row.get("executed") is not None else None,
            row_count=row.get("row_count"),
            warnings=json_loads(row.get("warnings_json"), []),
            created_at=as_datetime(row["created_at"]),
        )
