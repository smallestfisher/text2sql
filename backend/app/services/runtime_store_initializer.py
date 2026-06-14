from __future__ import annotations

import json

from backend.app.config import RUNTIME_STORE_SCHEMA_PATH
from backend.app.services.database_connector import DatabaseConnector


OBSOLETE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("query_logs", "plan_valid"),
    ("query_logs", "plan_risk_level"),
    ("query_logs", "plan_risk_flags_json"),
    ("sql_audit_logs", "plan_valid"),
    ("sql_audit_logs", "plan_risk_level"),
    ("sql_audit_logs", "plan_risk_flags_json"),
)


class RuntimeStoreInitializer:
    def __init__(
        self,
        database_connector: DatabaseConnector,
        schema_path=RUNTIME_STORE_SCHEMA_PATH,
    ) -> None:
        self.database_connector = database_connector
        self.schema_path = schema_path

    def ensure_schema(self) -> dict:
        if not self.database_connector.connected:
            raise RuntimeError("runtime database connector is not configured")
        database_result = self.database_connector.ensure_database_exists()
        if not database_result.get("executed"):
            raise RuntimeError(
                f"failed to ensure runtime database exists: {database_result.get('error') or database_result}"
            )
        sql_script = self.schema_path.read_text(encoding="utf-8")
        schema_result = self.database_connector.execute_script(sql_script)
        if not schema_result.get("executed"):
            raise RuntimeError(
                f"failed to initialize runtime schema: {schema_result.get('error') or schema_result}"
            )

        migration_errors: list[str] = []
        for table_name, column_name in OBSOLETE_COLUMNS:
            self._drop_column_if_exists(table_name, column_name, migration_errors)

        self._ensure_column(
            "query_logs",
            "effective_question",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "context_relation",
            "VARCHAR(32) NULL",
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "question_decision",
            "VARCHAR(32) NULL",
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "conversation_summary",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "semantic_brief",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "question_context_json",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "context_valid",
            "BOOLEAN NULL",
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "context_risk_level",
            "VARCHAR(16) NULL",
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "context_risk_flags_json",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "sql_risk_level",
            "VARCHAR(16) NULL",
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "sql_risk_flags_json",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "sql_audit_logs",
            "context_valid",
            "BOOLEAN NOT NULL",
            migration_errors,
        )
        self._ensure_column(
            "sql_audit_logs",
            "context_risk_level",
            "VARCHAR(16) NULL",
            migration_errors,
        )
        self._ensure_column(
            "sql_audit_logs",
            "context_risk_flags_json",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "sql_audit_logs",
            "sql_risk_level",
            "VARCHAR(16) NULL",
            migration_errors,
        )
        self._ensure_column(
            "sql_audit_logs",
            "sql_risk_flags_json",
            self._text_column_definition(),
            migration_errors,
        )
        self._ensure_column(
            "retrieval_logs",
            "summary",
            "TEXT NULL",
            migration_errors,
        )
        self._ensure_column(
            "retrieval_logs",
            "retrieval_channel",
            "VARCHAR(32) NULL",
            migration_errors,
        )
        self._ensure_column(
            "retrieval_logs",
            "source_score",
            "DOUBLE NULL",
            migration_errors,
        )

        self._ensure_index("chat_sessions", "idx_chat_sessions_updated_at", "updated_at", migration_errors)
        self._ensure_index("chat_sessions", "idx_chat_sessions_user_id", "user_id, updated_at", migration_errors)
        self._ensure_index("chat_messages", "idx_chat_messages_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("chat_messages", "idx_chat_messages_trace_id", "trace_id", migration_errors)
        self._ensure_index("session_state_snapshots", "idx_session_state_snapshots_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("session_state_snapshots", "idx_session_state_snapshots_trace_id", "trace_id", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_user_created", "user_id, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_domain_created", "subject_domain, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_decision_created", "question_decision, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_sql_risk_created", "sql_risk_level, created_at", migration_errors)
        self._ensure_index("query_risk_flags", "idx_query_risk_flags_flag_created", "flag, created_at", migration_errors)
        self._ensure_index("query_risk_flags", "idx_query_risk_flags_trace", "trace_id", migration_errors)
        self._ensure_index("retrieval_logs", "idx_retrieval_logs_trace_rank", "trace_id, rank_position", migration_errors)
        self._ensure_index("retrieval_logs", "idx_retrieval_logs_channel_created", "retrieval_channel, created_at", migration_errors)
        self._ensure_index("sql_audit_logs", "idx_sql_audit_logs_trace_created", "trace_id, created_at", migration_errors)
        self._ensure_index("feedback_logs", "idx_feedback_logs_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("feedback_logs", "idx_feedback_logs_trace_created", "trace_id, created_at", migration_errors)
        self._ensure_index("feedback_logs", "idx_feedback_logs_user_created", "user_id, created_at", migration_errors)
        self._ensure_index("evaluation_runs", "idx_evaluation_runs_created_at", "created_at", migration_errors)
        self._ensure_index(
            "vector_corpus_documents",
            "idx_vector_corpus_documents_source",
            "source_type, source_id",
            migration_errors,
        )
        self._sync_query_risk_flag_index(migration_errors)

        schema_result["database"] = database_result.get("database")
        if migration_errors:
            raise RuntimeError("; ".join(migration_errors))
        return schema_result

    def _ensure_column(
        self,
        table_name: str,
        column_name: str,
        column_definition: str,
        errors: list[str],
    ) -> None:
        try:
            existing = self._find_column(table_name, column_name)
            if existing is not None:
                return
            self.database_connector.execute_write(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
        except Exception as exc:
            errors.append(f"ensure column {table_name}.{column_name} failed: {exc}")

    def _drop_column_if_exists(
        self,
        table_name: str,
        column_name: str,
        errors: list[str],
    ) -> None:
        try:
            existing = self._find_column(table_name, column_name)
            if existing is None:
                return
            self.database_connector.execute_write(
                f"ALTER TABLE {table_name} DROP COLUMN {column_name}"
            )
        except Exception as exc:
            errors.append(f"drop obsolete column {table_name}.{column_name} failed: {exc}")

    def _ensure_index(
        self,
        table_name: str,
        index_name: str,
        columns_sql: str,
        errors: list[str],
    ) -> None:
        try:
            existing = self._find_index(table_name, index_name)
            if existing is not None:
                return
            self.database_connector.execute_write(
                f"CREATE INDEX {index_name} ON {table_name} ({columns_sql})"
            )
        except Exception as exc:
            errors.append(f"ensure index {index_name} on {table_name} failed: {exc}")

    def _find_column(self, table_name: str, column_name: str) -> dict | None:
        return self.database_connector.fetch_one(
            """
            SELECT COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """,
            {
                "table_name": table_name,
                "column_name": column_name,
            },
        )

    def _find_index(self, table_name: str, index_name: str) -> dict | None:
        return self.database_connector.fetch_one(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND INDEX_NAME = :index_name
            """,
            {
                "table_name": table_name,
                "index_name": index_name,
            },
        )

    def _sync_query_risk_flag_index(self, errors: list[str]) -> None:
        try:
            rows = self.database_connector.fetch_all(
                """
                SELECT trace_id, context_risk_flags_json, sql_risk_flags_json, created_at
                FROM query_logs q
                WHERE (
                    (context_risk_flags_json IS NOT NULL AND context_risk_flags_json <> '[]')
                    OR (sql_risk_flags_json IS NOT NULL AND sql_risk_flags_json <> '[]')
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM query_risk_flags f
                    WHERE f.trace_id = q.trace_id
                  )
                """
            )
            for row in rows:
                self._insert_query_risk_flags(
                    trace_id=row["trace_id"],
                    source="context",
                    flags=self._decode_flags(row.get("context_risk_flags_json")),
                    created_at=row["created_at"],
                )
                self._insert_query_risk_flags(
                    trace_id=row["trace_id"],
                    source="sql",
                    flags=self._decode_flags(row.get("sql_risk_flags_json")),
                    created_at=row["created_at"],
                )
        except Exception as exc:
            errors.append(f"sync query_risk_flags failed: {exc}")

    def _insert_query_risk_flags(
        self,
        *,
        trace_id: str,
        source: str,
        flags: list[str],
        created_at,
    ) -> None:
        for flag in dict.fromkeys(item for item in flags if item):
            self.database_connector.execute_write(
                """
                INSERT IGNORE INTO query_risk_flags (trace_id, source, flag, created_at)
                VALUES (:trace_id, :source, :flag, :created_at)
                """,
                {
                    "trace_id": trace_id,
                    "source": source,
                    "flag": flag,
                    "created_at": created_at,
                },
            )

    @staticmethod
    def _decode_flags(payload: str | None) -> list[str]:
        if not payload:
            return []
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    def _text_column_definition(self) -> str:
        return "LONGTEXT NULL"
