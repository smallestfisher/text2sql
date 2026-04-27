from __future__ import annotations

from dataclasses import dataclass, field
import re

from backend.app.models.query_plan import QueryPlan
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError
except Exception:  # pragma: no cover - optional dependency
    sqlglot = None
    exp = None
    ParseError = Exception


@dataclass
class SqlValidationResult:
    errors: list[str]
    warnings: list[str]
    risk_level: str = "low"
    risk_flags: list[str] = field(default_factory=list)


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
        high_risk_limit: int = 1000,
    ) -> None:
        self.ast_validator = ast_validator or SqlAstValidator()
        self.semantic_runtime = semantic_runtime
        self.max_limit = max_limit
        self.high_risk_limit = max(high_risk_limit, max_limit)

    def validate(
        self,
        sql: str | None,
        domain_config: dict,
        query_plan: QueryPlan | None = None,
        required_filter_fields: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        result = self.validate_detailed(
            sql,
            domain_config,
            query_plan=query_plan,
            required_filter_fields=required_filter_fields,
        )
        return result.errors, result.warnings

    def validate_detailed(
        self,
        sql: str | None,
        domain_config: dict,
        query_plan: QueryPlan | None = None,
        required_filter_fields: list[str] | None = None,
    ) -> SqlValidationResult:
        if sql is None:
            return SqlValidationResult(errors=["sql is empty"], warnings=[])

        errors: list[str] = []
        warnings: list[str] = []
        normalized_sql = f" {sql.lower()} "
        inspection = self.ast_validator.inspect(sql)

        stripped_sql = normalized_sql.strip()
        if not (stripped_sql.startswith("select") or stripped_sql.startswith("with")):
            errors.append("only SELECT statements are allowed")

        for keyword in self.FORBIDDEN_KEYWORDS:
            if keyword in normalized_sql:
                errors.append(f"forbidden keyword detected:{keyword.strip()}")

        physical_sources = set(domain_config.get("semantic_graph", {}).get("nodes", []))
        allowed_sources = set(physical_sources)
        allowed_sources.update(inspection.cte_names)

        used_sources = inspection.sources
        unknown_sources = [source for source in used_sources if source not in allowed_sources]
        if unknown_sources:
            errors.append(f"sql references unknown sources: {', '.join(unknown_sources)}")

        if query_plan is not None:
            expected_sources = set(query_plan.tables)
            expected_sources.update(inspection.cte_names)
            unexpected_sources = [source for source in used_sources if source not in expected_sources]
            if unexpected_sources:
                errors.append(f"sql references sources outside query plan: {', '.join(unexpected_sources)}")

            missing_plan_filters = [
                filter_item.field
                for filter_item in query_plan.filters
                if filter_item.field
                and not self._contains_any_field_reference(
                    inspection.where_clause,
                    self._field_candidates(query_plan, filter_item.field),
                )
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
                    if not self._field_candidates(query_plan, field).intersection(actual_group_by_fields)
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
                    if not self._field_candidates(query_plan, field).intersection(actual_order_by_fields)
                ]
                if missing_sort_fields:
                    warnings.append(
                        "sql does not preserve query plan sort fields: " + ", ".join(sorted(set(missing_sort_fields)))
                    )

            time_filter_errors = self._validate_time_context(query_plan, inspection.where_clause)
            errors.extend(time_filter_errors)

            version_errors = self._validate_version_context(query_plan, inspection.where_clause)
            errors.extend(version_errors)

            demand_version_projection_errors = self._validate_demand_version_projection(
                query_plan,
                sql,
            )
            errors.extend(demand_version_projection_errors)

            demand_month_errors = self._validate_horizontal_demand_month_mapping(
                query_plan,
                sql,
                used_sources,
            )
            errors.extend(demand_month_errors)

            limit_errors = self._validate_limit_consistency(query_plan, inspection.limit_value, inspection.has_limit)
            errors.extend(limit_errors)

            if query_plan.metrics and not query_plan.dimensions and inspection.functions and inspection.group_by_fields:
                warnings.append("sql groups aggregated metrics by extra fields not present in query plan")

            select_dimension_errors = self._validate_selected_dimensions(query_plan, inspection)
            errors.extend(select_dimension_errors)

        if required_filter_fields:
            if query_plan is None:
                missing_filter_fields = list(required_filter_fields)
            else:
                missing_filter_fields = [
                    field
                    for field in required_filter_fields
                    if not self._contains_any_field_reference(
                        inspection.where_clause,
                        self._field_candidates(query_plan, field),
                    )
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
                    self._contains_any_field_reference(
                        inspection.where_clause,
                        self._field_candidates(query_plan, field),
                    )
                    for field in time_fields
                ):
                    warning_message = "sql does not include a time filter; this may cause wide scans"
                    if len(used_sources) > 1:
                        warning_message += " across multiple sources"
                    warnings.append(warning_message)

        warnings.extend(self._build_risk_warnings(inspection, used_sources))

        if not inspection.has_limit:
            warnings.append("sql does not include LIMIT")
        elif inspection.limit_value is not None and inspection.limit_value > self.max_limit:
            errors.append(
                f"sql limit {inspection.limit_value} exceeds configured maximum {self.max_limit}"
            )

        ast_errors, ast_warnings = self.ast_validator.validate(sql)
        errors.extend(ast_errors)
        warnings.extend(ast_warnings)

        risk_flags = self._collect_risk_flags(errors, warnings)
        return SqlValidationResult(
            errors=errors,
            warnings=warnings,
            risk_level=self._risk_level_for_flags(risk_flags),
            risk_flags=risk_flags,
        )

    def _contains_field_reference(self, sql_fragment: str, field: str) -> bool:
        if not sql_fragment:
            return False
        return re.search(rf"\b{re.escape(field)}\b", sql_fragment, re.IGNORECASE) is not None

    def _contains_any_field_reference(self, sql_fragment: str, fields: set[str]) -> bool:
        return any(self._contains_field_reference(sql_fragment, field) for field in fields)

    def _field_candidates(self, query_plan: QueryPlan, logical_field: str) -> set[str]:
        candidates = {logical_field.lower()}
        if self.semantic_runtime is None:
            return candidates
        resolved = self.semantic_runtime.resolve_field_candidates(
            query_plan.subject_domain,
            query_plan.tables,
            logical_field,
        )
        candidates.update(item.lower() for item in resolved if item)
        return candidates

    def _validate_time_context(
        self,
        query_plan: QueryPlan,
        where_clause: str,
    ) -> list[str]:
        if self.semantic_runtime is None:
            return []
        if query_plan.time_context.grain == "unknown":
            return []

        time_fields = self.semantic_runtime.time_filter_fields(query_plan.subject_domain)
        if not time_fields:
            return []

        if not any(
            self._contains_any_field_reference(where_clause, self._field_candidates(query_plan, field))
            for field in time_fields
        ):
            return ["sql is missing required time filter from query plan"]
        return []

    def _validate_version_context(
        self,
        query_plan: QueryPlan,
        where_clause: str,
    ) -> list[str]:
        if query_plan.version_context is None or not query_plan.version_context.field:
            return []
        if self._contains_any_field_reference(
            where_clause,
            self._field_candidates(query_plan, query_plan.version_context.field),
        ):
            return []
        return ["sql is missing required version filter from query plan"]

    def _validate_limit_consistency(
        self,
        query_plan: QueryPlan,
        sql_limit: int | None,
        has_limit: bool,
    ) -> list[str]:
        if not has_limit:
            return []
        if sql_limit is None:
            return []
        if sql_limit > query_plan.limit:
            return [f"sql limit {sql_limit} exceeds query plan limit {query_plan.limit}"]
        return []

    def _validate_demand_version_projection(
        self,
        query_plan: QueryPlan,
        sql: str,
    ) -> list[str]:
        version_field = query_plan.version_context.field if query_plan.version_context else None
        if version_field != "PM_VERSION":
            return []

        if not {"p_demand", "v_demand"}.intersection(set(query_plan.tables)):
            return []

        if sqlglot is None or exp is None:
            return []

        try:
            statement = sqlglot.parse_one(sql, read="mysql")
        except ParseError:
            return []

        if statement is None:
            return []

        outer_from = statement.args.get("from")
        if outer_from is None:
            return []

        outer_sources = {
            table.name.lower()
            for table in outer_from.find_all(exp.Table)
            if getattr(table, "name", None)
        }
        if "demand_unpivot" not in outer_sources:
            return []

        outer_where = statement.find(exp.Where)
        if outer_where is None:
            return []

        outer_where_fields = {
            column.name.upper()
            for column in outer_where.find_all(exp.Column)
            if getattr(column, "name", None)
        }
        if version_field.upper() not in outer_where_fields:
            return []

        demand_cte = next(
            (
                cte
                for cte in statement.find_all(exp.CTE)
                if getattr(cte, "alias_or_name", "").lower() == "demand_unpivot"
            ),
            None,
        )
        if demand_cte is None:
            return []

        select_nodes = list(demand_cte.this.find_all(exp.Select))
        if not select_nodes:
            return []

        missing_projection = False
        for select_node in select_nodes:
            projected_fields = {
                expression.alias_or_name.upper()
                for expression in select_node.expressions
                if getattr(expression, "alias_or_name", None)
            }
            if version_field.upper() not in projected_fields:
                missing_projection = True
                break

        if missing_projection:
            return [
                "demand_unpivot is filtered by PM_VERSION outside the CTE, but the CTE does not project PM_VERSION in every UNION branch"
            ]
        return []

    def _validate_horizontal_demand_month_mapping(
        self,
        query_plan: QueryPlan,
        sql: str,
        used_sources: list[str],
    ) -> list[str]:
        has_demand_month_filter = any(item.field == "demand_month" for item in query_plan.filters)
        if not has_demand_month_filter:
            return []

        demand_sources = {"p_demand", "v_demand"}
        if not demand_sources.intersection(set(query_plan.tables) | set(used_sources)):
            return []

        raw_month_date_math_patterns = (
            r"date_add\s*\(\s*(?:`?\w+`?\.)?`?month`?\s*,\s*interval",
            r"adddate\s*\(\s*(?:`?\w+`?\.)?`?month`?\s*,",
            r"timestampadd\s*\(\s*month\s*,\s*[^,]+,\s*(?:`?\w+`?\.)?`?month`?\s*\)",
        )
        lowered_sql = sql.lower()
        if any(re.search(pattern, lowered_sql, re.IGNORECASE) for pattern in raw_month_date_math_patterns):
            return [
                "horizontal demand month mapping must not use date math on raw MONTH when target demand_month is compact YYYYMM; convert MONTH to a real date first and format back to YYYYMM, or map REQUIREMENT_QTY directly to base MONTH"
            ]
        return []

    def _validate_selected_dimensions(
        self,
        query_plan: QueryPlan,
        inspection,
    ) -> list[str]:
        if not query_plan.dimensions:
            return []
        select_fields = {field.lower() for field in inspection.select_fields}
        missing_dimensions = [
            field
            for field in query_plan.dimensions
            if not self._field_candidates(query_plan, field).intersection(select_fields)
        ]
        if missing_dimensions:
            return [
                "sql does not project required dimensions from query plan: "
                + ", ".join(sorted(set(missing_dimensions)))
            ]
        return []

    def _build_risk_warnings(self, inspection, used_sources: list[str]) -> list[str]:
        warnings: list[str] = []
        if inspection.has_wildcard_select:
            warnings.append("sql uses SELECT *; review result size and sensitive field exposure")
        if inspection.has_distinct:
            warnings.append("sql uses DISTINCT; verify whether deduplication changes business semantics")
        if inspection.has_having:
            warnings.append("sql uses HAVING; review aggregate filter semantics carefully")
        if inspection.has_subquery and len(used_sources) > 1:
            warnings.append("sql combines subquery and multiple sources; execution complexity may be high")
        if len(used_sources) >= 3:
            warnings.append("sql touches three or more sources; review join cardinality and execution risk")
        if len(inspection.functions) >= 4:
            warnings.append("sql contains many function calls; review complexity and semantic stability")
        if inspection.limit_value is not None and inspection.limit_value >= self.high_risk_limit:
            warnings.append(f"sql limit {inspection.limit_value} is high; review result size governance")
        if not inspection.has_limit and not inspection.has_where:
            warnings.append("sql has neither WHERE nor LIMIT; high full-scan risk")
        return warnings

    def _collect_risk_flags(self, errors: list[str], warnings: list[str]) -> list[str]:
        flags: list[str] = []
        for message in errors + warnings:
            lowered = message.lower()
            if "join" in lowered and "risk" in lowered:
                flags.append("join_risk")
            if "time filter" in lowered or "full-scan" in lowered or "wide scan" in lowered:
                flags.append("scan_risk")
            if "limit" in lowered and ("high" in lowered or "exceeds" in lowered):
                flags.append("result_size_risk")
            if "select *" in lowered or "sensitive field" in lowered:
                flags.append("exposure_risk")
            if "distinct" in lowered or "having" in lowered:
                flags.append("semantic_risk")
            if "subquery" in lowered or "complexity" in lowered or "many function calls" in lowered:
                flags.append("complexity_risk")
            if "permission filters" in lowered:
                flags.append("permission_risk")
            if "sources outside query plan" in lowered or "unsupported fields" in lowered:
                flags.append("plan_mismatch_risk")
        deduped: list[str] = []
        for flag in flags:
            if flag not in deduped:
                deduped.append(flag)
        return deduped

    def _risk_level_for_flags(self, risk_flags: list[str]) -> str:
        if any(flag in risk_flags for flag in ["permission_risk", "plan_mismatch_risk", "scan_risk", "join_risk"]):
            return "high"
        if risk_flags:
            return "medium"
        return "low"
