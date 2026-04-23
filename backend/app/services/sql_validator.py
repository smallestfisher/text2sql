from __future__ import annotations

import re

from backend.app.models.query_plan import QueryPlan
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator


class SqlValidator:
    FORBIDDEN_KEYWORDS = (
        " insert ",
        " update ",
        " delete ",
        " drop ",
        " alter ",
        " truncate ",
        " create ",
    )

    def __init__(
        self,
        ast_validator: SqlAstValidator | None = None,
        semantic_runtime: SemanticRuntime | None = None,
        max_limit: int = 200,
    ) -> None:
        self.ast_validator = ast_validator or SqlAstValidator()
        self.semantic_runtime = semantic_runtime
        self.max_limit = max_limit

    def validate(
        self,
        sql: str | None,
        semantic_layer: dict,
        query_plan: QueryPlan | None = None,
        required_filter_fields: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        if sql is None:
            return ["sql is empty"], []

        errors: list[str] = []
        warnings: list[str] = []
        normalized_sql = f" {sql.lower()} "
        inspection = self.ast_validator.inspect(sql)

        if not normalized_sql.strip().startswith("select"):
            errors.append("only SELECT statements are allowed")

        for keyword in self.FORBIDDEN_KEYWORDS:
            if keyword in normalized_sql:
                errors.append(f"forbidden keyword detected:{keyword.strip()}")

        allowed_sources = set(semantic_layer.get("semantic_graph", {}).get("nodes", []))
        allowed_sources.update(
            item["name"] for item in semantic_layer.get("semantic_views", [])
        )
        semantic_view_names = {
            item["name"] for item in semantic_layer.get("semantic_views", [])
        }

        used_sources = inspection.sources
        unknown_sources = [source for source in used_sources if source not in allowed_sources]
        if unknown_sources:
            errors.append(f"sql references unknown sources: {', '.join(unknown_sources)}")

        if query_plan is not None:
            expected_sources = set(query_plan.semantic_views + query_plan.tables)
            unexpected_sources = [source for source in used_sources if source not in expected_sources]
            if unexpected_sources:
                errors.append(f"sql references sources outside query plan: {', '.join(unexpected_sources)}")

            missing_plan_filters = [
                filter_item.field
                for filter_item in query_plan.filters
                if filter_item.field
                and not self._contains_field_reference(inspection.where_clause, filter_item.field)
            ]
            if missing_plan_filters:
                warnings.append(
                    "sql does not cover all query plan filters: " + ", ".join(sorted(set(missing_plan_filters)))
                )

            expected_dimension_fields = set(query_plan.dimensions)
            if expected_dimension_fields:
                actual_group_by_fields = {field.lower() for field in inspection.group_by_fields}
                missing_group_by_fields = [
                    field
                    for field in expected_dimension_fields
                    if field.lower() not in actual_group_by_fields
                ]
                if missing_group_by_fields and inspection.functions:
                    errors.append(
                        "sql does not group by required dimensions from query plan: "
                        + ", ".join(sorted(set(missing_group_by_fields)))
                    )

            expected_sort_fields = [item.field for item in query_plan.sort]
            if expected_sort_fields:
                actual_order_by_fields = {field.lower() for field in inspection.order_by_fields}
                missing_sort_fields = [
                    field
                    for field in expected_sort_fields
                    if field.lower() not in actual_order_by_fields
                ]
                if missing_sort_fields:
                    warnings.append(
                        "sql does not preserve query plan sort fields: " + ", ".join(sorted(set(missing_sort_fields)))
                    )

        if self.semantic_runtime is not None and used_sources:
            known_view_sources = [source for source in used_sources if source in semantic_view_names]
            if known_view_sources:
                allowed_fields: set[str] = set()
                for source in known_view_sources:
                    allowed_fields.update(self.semantic_runtime.semantic_view_fields(source))
                    for logical_field in self.semantic_runtime.semantic_view_fields(source):
                        allowed_fields.add(self.semantic_runtime.resolve_field(source, logical_field))
                unknown_field_refs = [
                    field for field in inspection.referenced_fields if field not in allowed_fields
                ]
                if unknown_field_refs:
                    errors.append(
                        "sql references unsupported fields for selected semantic views: "
                        + ", ".join(sorted(set(unknown_field_refs)))
                    )

        if required_filter_fields:
            missing_filter_fields = [
                field
                for field in required_filter_fields
                if not self._contains_field_reference(inspection.where_clause, field)
            ]
            if missing_filter_fields:
                errors.append(
                    f"sql is missing required permission filters: {', '.join(missing_filter_fields)}"
                )

        if len(used_sources) > 1:
            joins_without_condition = [join.source for join in inspection.joins if not join.has_condition]
            if joins_without_condition:
                errors.append(
                    "sql contains join without ON/USING condition: " + ", ".join(sorted(set(joins_without_condition)))
                )
            elif not inspection.joins:
                warnings.append("sql uses multiple sources but no explicit JOIN was detected; review for cartesian risk")

        if query_plan is not None and self.semantic_runtime is not None:
            if self.semantic_runtime.warn_if_missing_time_filter(query_plan.subject_domain):
                time_fields = self.semantic_runtime.time_filter_fields(query_plan.subject_domain)
                if time_fields and not any(
                    self._contains_field_reference(inspection.where_clause, field)
                    for field in time_fields
                ):
                    warning_message = "sql does not include a time filter; this may cause wide scans"
                    if len(used_sources) > 1:
                        warning_message += " across multiple sources"
                    warnings.append(warning_message)

        if not inspection.has_limit:
            warnings.append("sql does not include LIMIT")
        elif inspection.limit_value is not None and inspection.limit_value > self.max_limit:
            errors.append(
                f"sql limit {inspection.limit_value} exceeds configured maximum {self.max_limit}"
            )

        ast_errors, ast_warnings = self.ast_validator.validate(sql)
        errors.extend(ast_errors)
        warnings.extend(ast_warnings)

        return errors, warnings

    def _contains_field_reference(self, sql_fragment: str, field: str) -> bool:
        if not sql_fragment:
            return False
        return re.search(rf"\b{re.escape(field)}\b", sql_fragment, re.IGNORECASE) is not None
