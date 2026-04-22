from __future__ import annotations

from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.services.semantic_runtime import SemanticRuntime


class SqlGenerator:
    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime

    def generate(self, query_plan: QueryPlan, llm_sql: str | None = None) -> str | None:
        if query_plan.need_clarification:
            return None

        source_name = self._pick_source(query_plan)

        if llm_sql:
            return llm_sql

        if not source_name:
            return None

        dimensions = [self._resolve_field(source_name, item) for item in query_plan.dimensions]
        metric_selects = self._metric_selects(source_name, query_plan.metrics)
        if not metric_selects:
            return None

        select_parts = dimensions + metric_selects
        where_parts = self._where_clauses(source_name, query_plan.filters)

        sql_lines = [
            f"SELECT {', '.join(select_parts)}",
            f"FROM {source_name}",
        ]

        if where_parts:
            sql_lines.append(f"WHERE {' AND '.join(where_parts)}")

        if dimensions:
            sql_lines.append(f"GROUP BY {', '.join(dimensions)}")

        if query_plan.sort:
            sort_expr = ", ".join(
                f"{self._resolve_field(source_name, item.field)} {item.order.upper()}"
                for item in query_plan.sort
            )
            sql_lines.append(f"ORDER BY {sort_expr}")

        sql_lines.append(f"LIMIT {query_plan.limit}")
        return "\n".join(sql_lines) + ";"

    def _pick_source(self, query_plan: QueryPlan) -> str | None:
        if query_plan.semantic_views:
            return query_plan.semantic_views[0]
        if query_plan.tables:
            return query_plan.tables[0]
        return None

    def _metric_selects(self, source_name: str, metrics: list[str]) -> list[str]:
        return [
            f"{self._metric_aggregate(metric)}({self._resolve_field(source_name, self._metric_column(metric))}) AS {metric}"
            for metric in metrics
        ]

    def _metric_column(self, metric: str) -> str:
        if self.semantic_runtime is None:
            return metric
        return self.semantic_runtime.metric_column(metric)

    def _metric_aggregate(self, metric: str) -> str:
        if self.semantic_runtime is None:
            return "SUM"
        return self.semantic_runtime.metric_aggregate_function(metric)

    def _where_clauses(self, source_name: str, filters: list[FilterItem]) -> list[str]:
        return [self._render_filter(filter_item, source_name) for filter_item in filters]

    def _render_filter(self, filter_item: FilterItem, source_name: str | None) -> str:
        field = self._resolve_field(source_name, filter_item.field) if source_name else filter_item.field
        operator = filter_item.op
        value = filter_item.value

        if operator == "between" and isinstance(value, list) and len(value) == 2:
            return f"{field} BETWEEN {self._quote(value[0])} AND {self._quote(value[1])}"
        if operator == "in" and isinstance(value, list):
            values = ", ".join(self._quote(item) for item in value)
            return f"{field} IN ({values})"
        if operator == "is_null":
            return f"{field} IS NULL"
        if operator == "not_null":
            return f"{field} IS NOT NULL"
        return f"{field} {operator} {self._quote(value)}"

    def _quote(self, value) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, (int, float)):
            return str(value)
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    def _resolve_field(self, source_name: str | None, logical_field: str) -> str:
        if self.semantic_runtime is None:
            return logical_field
        return self.semantic_runtime.resolve_field(source_name, logical_field)
