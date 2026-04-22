from __future__ import annotations

from contextlib import contextmanager
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError, TimeoutError

from backend.app.models.api import ExecutionResponse


class DatabaseConnector:
    def __init__(
        self,
        database_url: str | None = None,
        timeout_seconds: int = 30,
        max_result_rows: int = 500,
        slow_query_threshold_ms: int = 3000,
    ) -> None:
        self.database_url = database_url
        self.timeout_seconds = timeout_seconds
        self.max_result_rows = max_result_rows
        self.slow_query_threshold_ms = slow_query_threshold_ms
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
                status="not_configured",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[],
                warnings=["database connector is not configured"],
                elapsed_ms=None,
                error_category="configuration",
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
                fetched_rows = result.fetchmany(self.max_result_rows + 1)
                truncated = len(fetched_rows) > self.max_result_rows
                rows = [dict(row._mapping) for row in fetched_rows[: self.max_result_rows]]
                columns = list(result.keys())
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if elapsed_ms >= self.slow_query_threshold_ms:
                    warnings.append(
                        f"slow query detected: {elapsed_ms} ms >= {self.slow_query_threshold_ms} ms"
                    )
                if truncated:
                    warnings.append(
                        f"result set truncated to {self.max_result_rows} rows"
                    )
                status = "ok"
                if not rows:
                    status = "empty_result"
                elif truncated:
                    status = "truncated"
                return ExecutionResponse(
                    executed=True,
                    status=status,
                    sql=sql,
                    row_count=len(rows),
                    columns=columns,
                    rows=rows,
                    errors=[],
                    warnings=warnings,
                    elapsed_ms=elapsed_ms,
                    truncated=truncated,
                )
        except TimeoutError as exc:
            return ExecutionResponse(
                executed=False,
                status="timeout",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[str(exc)],
                warnings=warnings,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error_category="timeout",
            )
        except OperationalError as exc:
            return ExecutionResponse(
                executed=False,
                status="db_error",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[str(exc)],
                warnings=warnings,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error_category="connectivity",
            )
        except ProgrammingError as exc:
            return ExecutionResponse(
                executed=False,
                status="db_error",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[str(exc)],
                warnings=warnings,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error_category="sql_runtime",
            )
        except SQLAlchemyError as exc:
            return ExecutionResponse(
                executed=False,
                status="db_error",
                sql=sql,
                row_count=0,
                columns=[],
                rows=[],
                errors=[str(exc)],
                warnings=warnings,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error_category="database",
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

    def fetch_all(self, sql: str, params: dict | None = None) -> list[dict]:
        if not self.connected:
            raise RuntimeError("database connector is not configured")
        with self.engine.connect() as connection:
            result = connection.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]

    def fetch_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.fetch_all(sql, params=params)
        return rows[0] if rows else None

    def execute_write(self, sql: str, params: dict | None = None) -> int:
        if not self.connected:
            raise RuntimeError("database connector is not configured")
        with self.engine.begin() as connection:
            result = connection.execute(text(sql), params or {})
            return int(result.rowcount or 0)

    @contextmanager
    def begin(self):
        if not self.connected:
            raise RuntimeError("database connector is not configured")
        with self.engine.begin() as connection:
            yield connection
