from __future__ import annotations

import re


class SqlQualityValidator:
    def validate(self, sql: str, inspection, used_sources: list[str]) -> list[str]:
        warnings: list[str] = []
        normalized = self._normalize(sql)
        if self._has_unprotected_division(normalized):
            warnings.append(
                "sql quality: division expression should guard zero denominators with NULLIF or CASE WHEN"
            )
        if self._is_multi_source_aggregate_without_cte(inspection, used_sources):
            warnings.append(
                "sql quality: multi-source aggregate query should use CTEs or derived tables to make aggregation grain explicit"
            )
        if re.search(r"\border\s+by\s+\d+\b", normalized):
            warnings.append("sql quality: avoid positional ORDER BY; use explicit output aliases or expressions")
        return warnings

    def _normalize(self, sql: str) -> str:
        compact = re.sub(r"\s+", " ", sql).strip().lower()
        compact = re.sub(r"'(?:''|[^'])*'", "''", compact)
        return compact

    def _has_unprotected_division(self, normalized_sql: str) -> bool:
        if "/" not in normalized_sql:
            return False
        if "nullif" in normalized_sql or "case when" in normalized_sql:
            return False
        return bool(re.search(r"[a-z0-9_\)\.]\s*/\s*[a-z0-9_\(]", normalized_sql))

    def _is_multi_source_aggregate_without_cte(self, inspection, used_sources: list[str]) -> bool:
        if len(used_sources) <= 1:
            return False
        if getattr(inspection, "cte_names", None):
            return False
        aggregate_functions = {"SUM", "COUNT", "AVG", "MIN", "MAX"}
        outer_functions = set(getattr(inspection, "outer_functions", []) or [])
        return bool(aggregate_functions.intersection(outer_functions))
