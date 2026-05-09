from __future__ import annotations

from backend.app.config import RUNTIME_STORE_SCHEMA_PATH
from backend.app.services.database_connector import DatabaseConnector
from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, MetaData, String, Table, Text


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
        if self.database_connector.sql_dialect.name == "oracle":
            schema_result = self._ensure_oracle_schema()
        else:
            sql_script = self.schema_path.read_text(encoding="utf-8")
            schema_result = self.database_connector.execute_script(sql_script)
            if not schema_result.get("executed"):
                raise RuntimeError(
                    f"failed to initialize runtime schema: {schema_result.get('error') or schema_result}"
                )

        migration_errors: list[str] = []
        self._ensure_column(
            "query_logs",
            "plan_risk_level",
            "VARCHAR(16) NULL",
            migration_errors,
        )
        self._ensure_column(
            "query_logs",
            "plan_risk_flags_json",
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
            "plan_risk_level",
            "VARCHAR(16) NULL",
            migration_errors,
        )
        self._ensure_column(
            "sql_audit_logs",
            "plan_risk_flags_json",
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

        self._ensure_index("chat_sessions", "idx_chat_sessions_updated_at", "updated_at", migration_errors)
        self._ensure_index("chat_sessions", "idx_chat_sessions_user_id", "user_id, updated_at", migration_errors)
        self._ensure_index("chat_messages", "idx_chat_messages_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("chat_messages", "idx_chat_messages_trace_id", "trace_id", migration_errors)
        self._ensure_index("session_state_snapshots", "idx_session_state_snapshots_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("session_state_snapshots", "idx_session_state_snapshots_trace_id", "trace_id", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_session_created", "session_id, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_user_created", "user_id, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_domain_created", "subject_domain, created_at", migration_errors)
        self._ensure_index("query_logs", "idx_query_logs_sql_risk_created", "sql_risk_level, created_at", migration_errors)
        self._ensure_index("retrieval_logs", "idx_retrieval_logs_trace_rank", "trace_id, rank_position", migration_errors)
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
            add_keyword = "ADD" if self.database_connector.sql_dialect.name == "oracle" else "ADD COLUMN"
            self.database_connector.execute_write(
                f"ALTER TABLE {table_name} {add_keyword} {column_name} {column_definition}"
            )
        except Exception as exc:
            errors.append(f"ensure column {table_name}.{column_name} failed: {exc}")

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
        if self.database_connector.sql_dialect.name == "oracle":
            return self.database_connector.fetch_one(
                """
                SELECT COLUMN_NAME
                FROM USER_TAB_COLUMNS
                WHERE TABLE_NAME = :table_name
                  AND COLUMN_NAME = :column_name
                """,
                {
                    "table_name": table_name.upper(),
                    "column_name": column_name.upper(),
                },
            )
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
        if self.database_connector.sql_dialect.name == "oracle":
            return self.database_connector.fetch_one(
                """
                SELECT INDEX_NAME
                FROM USER_INDEXES
                WHERE TABLE_NAME = :table_name
                  AND INDEX_NAME = :index_name
                """,
                {
                    "table_name": table_name.upper(),
                    "index_name": index_name.upper(),
                },
            )
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

    def _text_column_definition(self) -> str:
        return "CLOB NULL" if self.database_connector.sql_dialect.name == "oracle" else "LONGTEXT NULL"

    def _ensure_oracle_schema(self) -> dict:
        if self.database_connector.engine is None:
            return {"executed": False, "error": "runtime database connector is not configured"}
        metadata = MetaData()
        self._define_runtime_tables(metadata)
        metadata.create_all(self.database_connector.engine)
        return {"executed": True, "statements": len(metadata.tables), "sql_dialect": "oracle"}

    def _define_runtime_tables(self, metadata: MetaData) -> None:
        Table(
            "roles",
            metadata,
            Column("role_name", String(64), primary_key=True),
            Column("description", Text, nullable=True),
            Column("created_at", DateTime, nullable=False),
        )
        Table(
            "users",
            metadata,
            Column("user_id", String(64), primary_key=True),
            Column("username", String(191), nullable=False, unique=True),
            Column("password_hash", String(255), nullable=False),
            Column("is_active", Boolean, nullable=False),
            Column("created_at", DateTime, nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )
        Table(
            "user_roles",
            metadata,
            Column("user_id", String(64), primary_key=True),
            Column("role_name", String(64), primary_key=True),
            Column("created_at", DateTime, nullable=False),
        )
        chat_sessions = Table(
            "chat_sessions",
            metadata,
            Column("session_id", String(64), primary_key=True),
            Column("user_id", String(64), nullable=True),
            Column("title", Text, nullable=True),
            Column("status", String(32), nullable=False),
            Column("current_state_json", Text, nullable=True),
            Column("created_at", DateTime, nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )
        chat_messages = Table(
            "chat_messages",
            metadata,
            Column("message_id", String(64), primary_key=True),
            Column("session_id", String(64), nullable=False),
            Column("role", String(32), nullable=False),
            Column("content", Text, nullable=False),
            Column("trace_id", String(64), nullable=True),
            Column("created_at", DateTime, nullable=False),
        )
        session_state_snapshots = Table(
            "session_state_snapshots",
            metadata,
            Column("snapshot_id", String(64), primary_key=True),
            Column("session_id", String(64), nullable=False),
            Column("trace_id", String(64), nullable=True),
            Column("state_json", Text, nullable=False),
            Column("created_at", DateTime, nullable=False),
        )
        query_logs = Table(
            "query_logs",
            metadata,
            Column("trace_id", String(64), primary_key=True),
            Column("session_id", String(64), nullable=True),
            Column("user_id", String(64), nullable=True),
            Column("question", Text, nullable=True),
            Column("question_type", String(64), nullable=True),
            Column("subject_domain", String(64), nullable=True),
            Column("answer_status", String(64), nullable=True),
            Column("plan_valid", Boolean, nullable=True),
            Column("plan_risk_level", String(16), nullable=True),
            Column("plan_risk_flags_json", Text, nullable=True),
            Column("sql_valid", Boolean, nullable=True),
            Column("sql_risk_level", String(16), nullable=True),
            Column("sql_risk_flags_json", Text, nullable=True),
            Column("executed", Boolean, nullable=True),
            Column("row_count", Integer, nullable=True),
            Column("warnings_json", Text, nullable=True),
            Column("trace_json", Text, nullable=False),
            Column("created_at", DateTime, nullable=False),
        )
        retrieval_logs = Table(
            "retrieval_logs",
            metadata,
            Column("retrieval_log_id", String(64), primary_key=True),
            Column("trace_id", String(64), nullable=False),
            Column("rank_position", Integer, nullable=False),
            Column("source_type", String(64), nullable=False),
            Column("source_id", String(191), nullable=False),
            Column("score", Float, nullable=False),
            Column("matched_features_json", Text, nullable=True),
            Column("metadata_json", Text, nullable=True),
            Column("created_at", DateTime, nullable=False),
        )
        sql_audit_logs = Table(
            "sql_audit_logs",
            metadata,
            Column("sql_audit_id", String(64), primary_key=True),
            Column("trace_id", String(64), nullable=False),
            Column("sql_text", Text, nullable=True),
            Column("plan_valid", Boolean, nullable=False),
            Column("plan_risk_level", String(16), nullable=True),
            Column("plan_risk_flags_json", Text, nullable=True),
            Column("sql_valid", Boolean, nullable=False),
            Column("sql_risk_level", String(16), nullable=True),
            Column("sql_risk_flags_json", Text, nullable=True),
            Column("executed", Boolean, nullable=False),
            Column("row_count", Integer, nullable=True),
            Column("warnings_json", Text, nullable=True),
            Column("errors_json", Text, nullable=True),
            Column("created_at", DateTime, nullable=False),
        )
        feedback_logs = Table(
            "feedback_logs",
            metadata,
            Column("feedback_id", String(64), primary_key=True),
            Column("session_id", String(64), nullable=True),
            Column("trace_id", String(64), nullable=True),
            Column("user_id", String(64), nullable=True),
            Column("feedback_type", String(32), nullable=False),
            Column("comment", Text, nullable=True),
            Column("created_at", DateTime, nullable=False),
        )
        evaluation_runs = Table(
            "evaluation_runs",
            metadata,
            Column("run_id", String(64), primary_key=True),
            Column("case_count", Integer, nullable=False),
            Column("passed_count", Integer, nullable=False),
            Column("failed_count", Integer, nullable=False),
            Column("run_json", Text, nullable=False),
            Column("created_at", DateTime, nullable=False),
        )
        vector_corpus_documents = Table(
            "vector_corpus_documents",
            metadata,
            Column("document_id", String(64), primary_key=True),
            Column("source_type", String(64), nullable=False),
            Column("source_id", String(191), nullable=False),
            Column("summary", Text, nullable=True),
            Column("text_content", Text, nullable=False),
            Column("metadata_json", Text, nullable=True),
            Column("content_hash", String(64), nullable=False),
            Column("embedding_provider", String(64), nullable=False),
            Column("embedding_backend", String(64), nullable=False),
            Column("embedding_model", String(191), nullable=False),
            Column("embedding_dimensions", Integer, nullable=False),
            Column("vector_json", Text, nullable=False),
            Column("created_at", DateTime, nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )
        Index("idx_chat_sessions_updated_at", chat_sessions.c.updated_at)
        Index("idx_chat_sessions_user_id", chat_sessions.c.user_id, chat_sessions.c.updated_at)
        Index("idx_chat_messages_session_created", chat_messages.c.session_id, chat_messages.c.created_at)
        Index("idx_chat_messages_trace_id", chat_messages.c.trace_id)
        Index("idx_session_state_snapshots_session_created", session_state_snapshots.c.session_id, session_state_snapshots.c.created_at)
        Index("idx_session_state_snapshots_trace_id", session_state_snapshots.c.trace_id)
        Index("idx_query_logs_session_created", query_logs.c.session_id, query_logs.c.created_at)
        Index("idx_query_logs_user_created", query_logs.c.user_id, query_logs.c.created_at)
        Index("idx_query_logs_domain_created", query_logs.c.subject_domain, query_logs.c.created_at)
        Index("idx_query_logs_sql_risk_created", query_logs.c.sql_risk_level, query_logs.c.created_at)
        Index("idx_retrieval_logs_trace_rank", retrieval_logs.c.trace_id, retrieval_logs.c.rank_position)
        Index("idx_sql_audit_logs_trace_created", sql_audit_logs.c.trace_id, sql_audit_logs.c.created_at)
        Index("idx_feedback_logs_session_created", feedback_logs.c.session_id, feedback_logs.c.created_at)
        Index("idx_feedback_logs_trace_created", feedback_logs.c.trace_id, feedback_logs.c.created_at)
        Index("idx_feedback_logs_user_created", feedback_logs.c.user_id, feedback_logs.c.created_at)
        Index("idx_evaluation_runs_created_at", evaluation_runs.c.created_at)
        Index("idx_vector_corpus_documents_source", vector_corpus_documents.c.source_type, vector_corpus_documents.c.source_id)
