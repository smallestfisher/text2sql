from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SqlDialect:
    name: str

    @classmethod
    def from_name(cls, value: str | None, *, default: str = "mysql") -> "SqlDialect":
        lowered = (value or "").strip().lower()
        if not lowered:
            lowered = default
        if lowered not in {"oracle", "mysql"}:
            raise ValueError(f"unsupported sql dialect: {value}")
        return cls(lowered)

    @property
    def label(self) -> str:
        return "Oracle SQL" if self.name == "oracle" else "MySQL"

    @property
    def sqlglot_dialect(self) -> str:
        return "oracle" if self.name == "oracle" else "mysql"

    @property
    def result_limit_clause_name(self) -> str:
        return "FETCH FIRST n ROWS ONLY" if self.name == "oracle" else "LIMIT n"

    def result_limit_clause(self, limit: int) -> str:
        if self.name == "oracle":
            return f"FETCH FIRST {int(limit)} ROWS ONLY"
        return f"LIMIT {int(limit)}"

    def has_result_limit(self, sql: str) -> bool:
        if re.search(r"\bLIMIT\s+\d+\b", sql, re.IGNORECASE):
            return True
        return re.search(
            r"\bFETCH\s+(?:FIRST|NEXT)\s+\d+\s+ROWS\s+ONLY\b",
            sql,
            re.IGNORECASE,
        ) is not None

    def extract_result_limit_value(self, sql: str) -> int | None:
        limit_match = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
        if limit_match:
            return int(limit_match.group(1))
        fetch_match = re.search(
            r"\bFETCH\s+(?:FIRST|NEXT)\s+(\d+)\s+ROWS\s+ONLY\b",
            sql,
            re.IGNORECASE,
        )
        if fetch_match:
            return int(fetch_match.group(1))
        return None

    def strip_statement_terminator(self, sql: str) -> str:
        return sql.strip().rstrip(";").strip()

    def apply_read_timeout(self, connection, timeout_seconds: int) -> None:
        if timeout_seconds <= 0:
            return
        if self.name == "mysql":
            connection.exec_driver_sql(
                f"SET SESSION MAX_EXECUTION_TIME={timeout_seconds * 1000}"
            )
            return
        if self.name == "oracle":
            raw_connection = getattr(connection, "connection", None)
            driver_connection = getattr(raw_connection, "driver_connection", raw_connection)
            if hasattr(driver_connection, "call_timeout"):
                driver_connection.call_timeout = timeout_seconds * 1000
                return
            if hasattr(driver_connection, "callTimeout"):
                driver_connection.callTimeout = timeout_seconds * 1000
                return
            raise RuntimeError("oracle driver does not expose call timeout configuration")
