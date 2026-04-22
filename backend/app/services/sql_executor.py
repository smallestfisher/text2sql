from __future__ import annotations

import re

from backend.app.models.api import ExecutionResponse
from backend.app.models.auth import UserContext
from backend.app.services.database_connector import DatabaseConnector


class SqlExecutor:
    def __init__(
        self,
        database_connector: DatabaseConnector | None = None,
        max_sql_length: int = 20000,
    ) -> None:
        self.database_connector = database_connector or DatabaseConnector()
        self.max_sql_length = max_sql_length

    def execute(self, sql: str | None, user_context: UserContext | None = None) -> ExecutionResponse:
        if sql is None:
            return ExecutionResponse(
                executed=False,
                status="sql_missing",
                sql=None,
                row_count=0,
                columns=[],
                rows=[],
                errors=["sql is empty"],
                warnings=[],
                elapsed_ms=None,
                error_category="planner",
            )

        if user_context is not None and not user_context.can_execute_sql:
            return ExecutionResponse(
                executed=False,
                status="permission_denied",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=["user is not allowed to execute SQL"],
                warnings=[],
                elapsed_ms=None,
                error_category="permission",
            )

        blocked_reason = self._preflight_block_reason(sql)
        if blocked_reason is not None:
            return ExecutionResponse(
                executed=False,
                status="blocked",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[blocked_reason],
                warnings=[],
                elapsed_ms=None,
                error_category="governance",
            )

        return self.database_connector.execute_readonly(sql)

    def health(self) -> dict:
        return self.database_connector.test_connection()


    def _preflight_block_reason(self, sql: str) -> str | None:
        normalized = sql.strip()
        if len(normalized) > self.max_sql_length:
            return f"sql text exceeds configured maximum length {self.max_sql_length}"
        if re.search(r"(^|\s)--", normalized) or "/*" in normalized:
            return "sql comments are not allowed in execution stage"
        if re.search(r"\bFOR\s+UPDATE\b", normalized, re.IGNORECASE):
            return "FOR UPDATE is not allowed in readonly execution stage"
        if re.search(r"\bINTO\s+OUTFILE\b", normalized, re.IGNORECASE):
            return "INTO OUTFILE is not allowed in execution stage"
        return None
