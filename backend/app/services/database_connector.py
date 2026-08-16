from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
import re
import time

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError, TimeoutError

from backend.app.models.api import ExecutionResponse
from backend.app.services.sql_dialect import SqlDialect


logger = logging.getLogger(__name__)


class DatabaseConnector:
    def __init__(
        self,
        database_url: str | None = None,
        timeout_seconds: int = 30,
        max_result_rows: int = 500,
        slow_query_threshold_ms: int = 3000,
        sql_dialect: str | None = None,
    ) -> None:
        self.database_url = database_url
        self.timeout_seconds = timeout_seconds
        self.max_result_rows = max_result_rows
        self.slow_query_threshold_ms = slow_query_threshold_ms
        self.sql_dialect = SqlDialect.from_name(sql_dialect)
        self.engine = self._create_engine(database_url) if database_url else None
        logger.debug(
            "database connector configured dialect=%s configured=%s timeout_seconds=%s max_rows=%s",
            self.sql_dialect.name,
            bool(database_url),
            self.timeout_seconds,
            self.max_result_rows,
        )

    @property
    def connected(self) -> bool:
        return self.engine is not None

    def dispose(self) -> None:
        """Release the underlying connection pool. Called when the owning
        container is rebuilt so stale engines don't leak connections."""
        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.warning("failed to dispose database engine", exc_info=True)

    def execute_readonly(self, sql: str) -> ExecutionResponse:
        if not self.connected:
            raise RuntimeError("database connector is not configured")

        started = time.perf_counter()
        warnings: list[str] = []
        try:
            executable_sql = self.sql_dialect.strip_statement_terminator(sql)
            logger.debug(
                "sql execute start dialect=%s preview=%s",
                self.sql_dialect.name,
                self._preview_sql(executable_sql),
            )
            with self.engine.connect() as connection:
                if self.timeout_seconds > 0:
                    try:
                        self._apply_session_max_execution_time(connection)
                    except (RuntimeError, SQLAlchemyError) as exc:
                        return ExecutionResponse(
                            executed=False,
                            status="db_error",
                            sql=sql,
                            row_count=0,
                            columns=[],
                            rows=[],
                            errors=[f"failed to apply session max execution time: {exc}"],
                            warnings=warnings,
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            error_category="configuration",
                        )
                result = connection.execute(text(self._prepare_sql(executable_sql)))
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
                logger.debug(
                    "sql execute done status=%s rows=%s truncated=%s elapsed_ms=%s columns=%s",
                    status,
                    len(rows),
                    truncated,
                    elapsed_ms,
                    columns,
                )
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
            logger.warning(
                "sql execute timeout elapsed_ms=%s error=%s",
                int((time.perf_counter() - started) * 1000),
                exc,
            )
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
            logger.warning(
                "sql execute connectivity error elapsed_ms=%s error=%s",
                int((time.perf_counter() - started) * 1000),
                exc,
            )
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
            logger.warning(
                "sql execute runtime error elapsed_ms=%s error=%s",
                int((time.perf_counter() - started) * 1000),
                exc,
            )
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
            logger.warning(
                "sql execute database error elapsed_ms=%s error=%s",
                int((time.perf_counter() - started) * 1000),
                exc,
            )
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

    def test_connection(self, *, verify_readonly_session_settings: bool = False) -> dict:
        if not self.connected:
            return {
                "connected": False,
                "error": "database connector is not configured",
                "database_url_configured": False,
                "sql_dialect": self.sql_dialect.name,
                "timeout_seconds": self.timeout_seconds,
                "max_result_rows": self.max_result_rows,
                "slow_query_threshold_ms": self.slow_query_threshold_ms,
            }
        try:
            with self.engine.connect() as connection:
                if verify_readonly_session_settings and self.timeout_seconds > 0:
                    self._apply_session_max_execution_time(connection)
                connection.execute(text("SELECT 1"))
            logger.debug(
                "database health ok dialect=%s verify_readonly=%s",
                self.sql_dialect.name,
                verify_readonly_session_settings,
            )
            return {
                "connected": True,
                "database_url_configured": True,
                "sql_dialect": self.sql_dialect.name,
                "timeout_seconds": self.timeout_seconds,
                "max_result_rows": self.max_result_rows,
                "slow_query_threshold_ms": self.slow_query_threshold_ms,
            }
        except (RuntimeError, SQLAlchemyError) as exc:
            logger.warning(
                "database health failed dialect=%s verify_readonly=%s error=%s",
                self.sql_dialect.name,
                verify_readonly_session_settings,
                exc,
            )
            return {
                "connected": False,
                "error": str(exc),
                "database_url_configured": True,
                "sql_dialect": self.sql_dialect.name,
                "timeout_seconds": self.timeout_seconds,
                "max_result_rows": self.max_result_rows,
                "slow_query_threshold_ms": self.slow_query_threshold_ms,
            }

    def execute_script(self, sql_script: str) -> dict:
        if not self.connected:
            return {"executed": False, "error": "database connector is not configured"}
        try:
            statements = [segment.strip() for segment in sql_script.split(";") if segment.strip()]
            with self.engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(self._prepare_sql(statement)))
            return {"executed": True, "statements": len(statements)}
        except SQLAlchemyError as exc:
            return {"executed": False, "error": str(exc)}

    def fetch_all(self, sql: str, params: dict | None = None) -> list[dict]:
        if not self.connected:
            raise RuntimeError("database connector is not configured")
        with self.engine.connect() as connection:
            result = connection.execute(text(self._prepare_sql(sql)), params or {})
            return [dict(row._mapping) for row in result]

    def fetch_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.fetch_all(sql, params=params)
        return rows[0] if rows else None

    def execute_write(self, sql: str, params: dict | None = None) -> int:
        if not self.connected:
            raise RuntimeError("database connector is not configured")
        with self.engine.begin() as connection:
            result = connection.execute(text(self._prepare_sql(sql)), params or {})
            return int(result.rowcount or 0)

    def ensure_database_exists(self) -> dict:
        if not self.database_url:
            return {"executed": False, "error": "database connector is not configured"}

        target_url = make_url(self.database_url)
        target_database = target_url.database
        if not target_database:
            return {"executed": False, "error": "target database name is missing"}

        if self.sql_dialect.name == "sqlite":
            self._ensure_sqlite_parent(target_database)
            try:
                with self.engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return {"executed": True, "database": target_database}
            except SQLAlchemyError as exc:
                return {"executed": False, "error": str(exc), "database": target_database}

        admin_engine = create_engine(
            target_url.set(database=None),
            pool_pre_ping=True,
            future=True,
        )
        try:
            with admin_engine.begin() as connection:
                escaped_database = target_database.replace("`", "``")
                connection.execute(
                    text(
                        f"CREATE DATABASE IF NOT EXISTS `{escaped_database}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
                )
            return {"executed": True, "database": target_database}
        except SQLAlchemyError as exc:
            return {"executed": False, "error": str(exc), "database": target_database}
        finally:
            admin_engine.dispose()

    @contextmanager
    def begin(self):
        if not self.connected:
            raise RuntimeError("database connector is not configured")
        if self.sql_dialect.name == "sqlite":
            with self.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    yield connection
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
            return
        with self.engine.begin() as connection:
            yield connection

    def _create_engine(self, database_url: str):
        options: dict = {"pool_pre_ping": True, "future": True}
        if self.sql_dialect.name == "sqlite":
            target_database = make_url(database_url).database
            if target_database:
                self._ensure_sqlite_parent(target_database)
            options["connect_args"] = {
                "check_same_thread": False,
                "timeout": max(self.timeout_seconds, 5),
            }
        engine = create_engine(database_url, **options)
        if self.sql_dialect.name == "sqlite":
            busy_timeout_ms = max(self.timeout_seconds, 5) * 1000

            @event.listens_for(engine, "connect")
            def configure_sqlite(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                finally:
                    cursor.close()
        return engine

    @staticmethod
    def _ensure_sqlite_parent(database: str) -> None:
        if database == ":memory:":
            return
        Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)

    def _apply_session_max_execution_time(self, connection) -> None:
        self.sql_dialect.apply_read_timeout(connection, self.timeout_seconds)

    def _prepare_sql(self, sql: str) -> str:
        return self.sql_dialect.strip_statement_terminator(sql)

    @staticmethod
    def _preview_sql(sql: str, max_length: int = 180) -> str:
        compact = re.sub(r"\s+", " ", sql).strip()
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3] + "..."
