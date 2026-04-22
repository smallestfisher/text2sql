from __future__ import annotations

import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.models.api import ExecutionResponse


class DatabaseConnector:
    def __init__(self, database_url: str | None = None, timeout_seconds: int = 30) -> None:
        self.database_url = database_url
        self.timeout_seconds = timeout_seconds
        self.engine = (
            create_engine(database_url, pool_pre_ping=True, future=True)
            if database_url
            else None
        )

    @property
    def connected(self) -> bool:
        return self.engine is not None

    def execute_readonly(self, sql: str) -> ExecutionResponse:
        if not self.connected:
            return ExecutionResponse(
                executed=False,
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[],
                warnings=["database connector is not configured"],
                elapsed_ms=None,
            )

        started = time.perf_counter()
        warnings: list[str] = []
        try:
            with self.engine.connect() as connection:
                if self.timeout_seconds > 0:
                    try:
                        connection.exec_driver_sql(
                            f"SET SESSION MAX_EXECUTION_TIME={self.timeout_seconds * 1000}"
                        )
                    except SQLAlchemyError:
                        warnings.append("failed to apply session max execution time")
                result = connection.execute(text(sql))
                rows = [dict(row._mapping) for row in result]
                columns = list(result.keys())
                return ExecutionResponse(
                    executed=True,
                    sql=sql,
                    row_count=len(rows),
                    columns=columns,
                    rows=rows,
                    errors=[],
                    warnings=warnings,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
        except SQLAlchemyError as exc:
            return ExecutionResponse(
                executed=False,
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[str(exc)],
                warnings=warnings,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )

    def test_connection(self) -> dict:
        if not self.connected:
            return {"connected": False, "error": "database connector is not configured"}
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {"connected": True}
        except SQLAlchemyError as exc:
            return {"connected": False, "error": str(exc)}

    def execute_script(self, sql_script: str) -> dict:
        if not self.connected:
            return {"executed": False, "error": "database connector is not configured"}
        try:
            statements = [segment.strip() for segment in sql_script.split(";") if segment.strip()]
            with self.engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))
            return {"executed": True, "statements": len(statements)}
        except SQLAlchemyError as exc:
            return {"executed": False, "error": str(exc)}
