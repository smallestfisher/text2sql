from __future__ import annotations

from backend.app.models.api import ExecutionResponse
from backend.app.models.auth import UserContext
from backend.app.services.database_connector import DatabaseConnector


class SqlExecutor:
    def __init__(self, database_connector: DatabaseConnector | None = None) -> None:
        self.database_connector = database_connector or DatabaseConnector()

    def execute(self, sql: str | None, user_context: UserContext | None = None) -> ExecutionResponse:
        if sql is None:
            return ExecutionResponse(
                executed=False,
                sql=None,
                row_count=0,
                columns=[],
                rows=[],
                errors=["sql is empty"],
                warnings=[],
                elapsed_ms=None,
            )

        if user_context is not None and not user_context.can_execute_sql:
            return ExecutionResponse(
                executed=False,
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=["user is not allowed to execute SQL"],
                warnings=[],
                elapsed_ms=None,
            )

        return self.database_connector.execute_readonly(sql)

    def health(self) -> dict:
        return self.database_connector.test_connection()
