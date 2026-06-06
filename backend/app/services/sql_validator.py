from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re

from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_dialect import SqlDialect
from backend.app.services.sql_quality_validator import SqlQualityValidator


@dataclass
class SqlValidationResult:
    errors: list[str]
    warnings: list[str]
    risk_level: str = "low"
    risk_flags: list[str] = field(default_factory=list)


logger = logging.getLogger(__name__)


class SqlValidator:
    FORBIDDEN_KEYWORDS = (
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
        "create",
        "merge",
        "grant",
        "revoke",
        "execute",
    )

    def __init__(
        self,
        ast_validator: SqlAstValidator | None = None,
        semantic_runtime: SemanticRuntime | None = None,
        max_limit: int = 200,
        high_risk_limit: int = 1000,
        quality_validator: SqlQualityValidator | None = None,
    ) -> None:
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.ast_validator = ast_validator or SqlAstValidator()
        self.semantic_runtime = semantic_runtime
        self.max_limit = max_limit
        self.high_risk_limit = max(high_risk_limit, max_limit)
        self.quality_validator = quality_validator or SqlQualityValidator()

    def validate(
        self,
        sql: str | None,
        domain_config: dict,
        sql_context: SqlGenerationContext | None = None,
        required_filter_fields: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        result = self.validate_detailed(
            sql,
            domain_config,
            sql_context=sql_context,
            required_filter_fields=required_filter_fields,
        )
        return result.errors, result.warnings

    def validate_detailed(
        self,
        sql: str | None,
        domain_config: dict,
        sql_context: SqlGenerationContext | None = None,
        required_filter_fields: list[str] | None = None,
    ) -> SqlValidationResult:
        if sql is None:
            return SqlValidationResult(errors=["sql is empty"], warnings=[])

        errors: list[str] = []
        warnings: list[str] = []
        safety_sql = self._sql_without_literals_and_comments(sql)
        normalized_sql = f" {safety_sql.lower()} "
        inspection = self.ast_validator.inspect(sql)
        logger.info(
            "sql validator inspect parser=%s statements=%s sources=%s select_fields=%s group_by_fields=%s functions=%s",
            inspection.parser_backend,
            inspection.statement_count,
            inspection.sources,
            inspection.select_fields,
            inspection.group_by_fields,
            inspection.functions,
        )
        filter_scope = inspection.all_where_clause or inspection.where_clause

        stripped_sql = normalized_sql.strip()
        if not (stripped_sql.startswith("select") or stripped_sql.startswith("with")):
            errors.append("only SELECT statements are allowed")

        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", normalized_sql, re.IGNORECASE):
                errors.append(f"forbidden keyword detected:{keyword}")

        physical_sources = set(domain_config.get("semantic_graph", {}).get("nodes", []))
        allowed_sources = set(physical_sources)
        allowed_sources.update(inspection.cte_names)

        used_sources = list(dict.fromkeys(inspection.sources))
        unknown_sources = [source for source in used_sources if source not in allowed_sources]
        if unknown_sources:
            errors.append(f"sql references unknown sources: {', '.join(unknown_sources)}")

        if sql_context is not None:
            expected_sources = set(sql_context.tables)
            expected_sources.update(inspection.cte_names)
            unexpected_sources = [source for source in used_sources if source not in expected_sources]
            if unexpected_sources:
                errors.append(f"sql references sources outside sql context: {', '.join(unexpected_sources)}")

            missing_context_filters = [
                filter_item.field
                for filter_item in sql_context.filters
                if filter_item.field
                and self._is_sql_enforceable_filter_field(filter_item.field)
                and not self._filter_is_covered(
                    sql_context,
                    filter_item.field,
                    filter_scope,
                )
            ]
            if missing_context_filters:
                warnings.append(
                    "sql does not cover all sql context filters: " + ", ".join(sorted(set(missing_context_filters)))
                )

            expected_dimension_fields = set(sql_context.dimensions)
            if expected_dimension_fields:
                actual_group_by_fields = {field.lower() for field in inspection.group_by_fields}
                missing_group_by_fields = [
                    field
                    for field in expected_dimension_fields
                    if not self._field_candidates(sql_context, field).intersection(actual_group_by_fields)
                ]
                if missing_group_by_fields and any(
                    function in self.ast_validator.AGGREGATE_FUNCTIONS
                    for function in inspection.outer_functions
                ):
                    warnings.append(
                        "sql does not group by required dimensions from sql context: "
                        + ", ".join(sorted(set(missing_group_by_fields)))
                    )

            expected_sort_fields = [item.field for item in sql_context.sort]
            if expected_sort_fields:
                actual_order_by_fields = {field.lower() for field in inspection.order_by_fields}
                missing_sort_fields = [
                    field
                    for field in expected_sort_fields
                    if not self._sort_field_candidates(sql_context, field).intersection(actual_order_by_fields)
                ]
                if missing_sort_fields:
                    warnings.append(
                        "sql does not preserve sql context sort fields: " + ", ".join(sorted(set(missing_sort_fields)))
                    )

            time_filter_warnings = self._validate_time_context(sql_context, filter_scope)
            warnings.extend(time_filter_warnings)
            month_filter_semantic_warnings = self._validate_month_filter_semantics(sql_context, filter_scope)
            warnings.extend(month_filter_semantic_warnings)
            time_literal_format_warnings = self._validate_time_literal_formats(sql_context, filter_scope)
            warnings.extend(time_literal_format_warnings)

            version_warnings = self._validate_version_context(sql_context, filter_scope)
            warnings.extend(version_warnings)

            limit_warnings = self._validate_limit_consistency(sql_context, inspection.limit_value, inspection.has_limit)
            warnings.extend(limit_warnings)

            if sql_context.metrics and not sql_context.dimensions and inspection.functions and inspection.group_by_fields:
                warnings.append("sql groups aggregated metrics by extra fields not present in sql context")

            select_dimension_warnings = self._validate_selected_dimensions(sql_context, inspection)
            warnings.extend(select_dimension_warnings)

            unexpected_group_by_warnings = self._validate_unexpected_group_by_fields(sql_context, inspection)
            warnings.extend(unexpected_group_by_warnings)

        if required_filter_fields:
            if sql_context is None:
                missing_filter_fields = list(required_filter_fields)
            else:
                missing_filter_fields = [
                    field
                    for field in required_filter_fields
                    if not self._filter_is_covered(
                        sql_context,
                        field,
                        filter_scope,
                    )
                ]
            if missing_filter_fields:
                errors.append(
                    f"sql is missing required permission filters: {', '.join(missing_filter_fields)}"
                )

        if len(used_sources) > 1:
            joins_without_condition = [join.source for join in inspection.joins if not join.has_condition]
            if joins_without_condition:
                warnings.append(
                    "sql contains join without ON/USING condition: " + ", ".join(sorted(set(joins_without_condition)))
                )
            elif not inspection.joins:
                warnings.append("sql uses multiple sources but no explicit JOIN was detected; review for cartesian risk")

        if sql_context is not None and self.semantic_runtime is not None:
            if self.semantic_runtime.warn_if_missing_time_filter(sql_context.subject_domain):
                time_fields = self.semantic_runtime.time_filter_fields(sql_context.subject_domain)
                if time_fields and not any(
                    self._filter_is_covered(sql_context, field, filter_scope)
                    for field in time_fields
                ):
                    warning_message = "sql does not include a time filter; this may cause wide scans"
                    if len(used_sources) > 1:
                        warning_message += " across multiple sources"
                    warnings.append(warning_message)

        warnings.extend(self._build_risk_warnings(inspection, used_sources))
        warnings.extend(self.quality_validator.validate(sql, inspection, used_sources))

        if not inspection.has_limit:
            warnings.append(f"sql does not include {self.sql_dialect.result_limit_clause_name}")
        elif inspection.limit_value is not None and inspection.limit_value > self.max_limit:
            warnings.append(
                f"sql result limit {inspection.limit_value} exceeds configured maximum {self.max_limit}"
            )

        ast_errors, ast_warnings = self.ast_validator.validate(sql)
        errors.extend(ast_errors)
        warnings.extend(ast_warnings)

        risk_flags = self._collect_risk_flags(errors, warnings)
        logger.info(
            "sql validator result valid=%s errors=%s warnings=%s risk_level=%s risk_flags=%s",
            not errors,
            errors,
            warnings,
            self._risk_level_for_flags(risk_flags),
            risk_flags,
        )
        return SqlValidationResult(
            errors=errors,
            warnings=warnings,
            risk_level=self._risk_level_for_flags(risk_flags),
            risk_flags=risk_flags,
        )

    def _sql_without_literals_and_comments(self, sql: str) -> str:
        stripped = re.sub(r"--.*?(?=\n|$)", " ", sql)
        stripped = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.DOTALL)
        stripped = re.sub(r"'(?:''|[^'])*'", "''", stripped)
        stripped = re.sub(r'"(?:""|[^"])*"', '""', stripped)
        return stripped

    def _contains_field_reference(self, sql_fragment: str, field: str) -> bool:
        if not sql_fragment:
            return False
        return re.search(rf"\b{re.escape(field)}\b", sql_fragment, re.IGNORECASE) is not None

    def _is_sql_enforceable_filter_field(self, logical_field: str) -> bool:
        return logical_field not in {"source_table", "demand_source"}

    def _contains_any_field_reference(self, sql_fragment: str, fields: set[str]) -> bool:
        return any(self._contains_field_reference(sql_fragment, field) for field in fields)

    def _field_candidates(self, sql_context: SqlGenerationContext, logical_field: str) -> set[str]:
        candidates = {logical_field.lower()}
        if self.semantic_runtime is None:
            return candidates
        resolved = self.semantic_runtime.resolve_field_candidates(
            sql_context.subject_domain,
            sql_context.tables,
            logical_field,
        )
        physical_candidates = self._physical_field_candidates(sql_context, resolved)
        if physical_candidates:
            return physical_candidates | candidates
        candidates.update(item.lower() for item in resolved if item)
        return candidates

    def _filter_is_covered(
        self,
        sql_context: SqlGenerationContext,
        logical_field: str,
        sql_fragment: str,
    ) -> bool:
        if self._contains_any_field_reference(sql_fragment, self._field_candidates(sql_context, logical_field)):
            return True
        if logical_field == "biz_month":
            return self._contains_any_field_reference(
                sql_fragment,
                self._field_candidates(sql_context, "biz_date"),
            )
        return False

    def _sort_field_candidates(self, sql_context: SqlGenerationContext, logical_field: str) -> set[str]:
        candidates = self._field_candidates(sql_context, logical_field)
        if self.semantic_runtime is None:
            return candidates
        metric_columns = {
            self.semantic_runtime.metric_column(metric_name).lower()
            for metric_name in sql_context.metrics
            if self.semantic_runtime.metric_column(metric_name)
        }
        if logical_field.lower() in metric_columns:
            candidates.add(logical_field.lower())
        return candidates

    def _physical_field_candidates(self, sql_context: SqlGenerationContext, resolved_fields: set[str]) -> set[str]:
        if self.semantic_runtime is None:
            return set()
        physical_allowed: set[str] = set()
        for table_name in sql_context.tables:
            physical_allowed.update(self.semantic_runtime.table_fields(table_name))
        for metric_name in sql_context.metrics:
            physical_allowed.update(
                self.semantic_runtime.metric_expression_columns(
                    metric_name,
                    table_names=sql_context.tables,
                )
            )
        return {
            item.lower()
            for item in resolved_fields
            if item and item in physical_allowed
        }

    def _validate_time_context(
        self,
        sql_context: SqlGenerationContext,
        where_clause: str,
    ) -> list[str]:
        if self.semantic_runtime is None:
            return []
        if sql_context.time_context.grain == "unknown":
            return []

        time_fields = self.semantic_runtime.time_filter_fields(sql_context.subject_domain)
        if not time_fields:
            return []

        if not any(
            self._contains_any_field_reference(where_clause, self._field_candidates(sql_context, field))
            for field in time_fields
        ):
            return ["sql is missing required time filter from sql context"]
        return []

    def _validate_version_context(
        self,
        sql_context: SqlGenerationContext,
        where_clause: str,
    ) -> list[str]:
        if sql_context.version_context is None or not sql_context.version_context.field:
            return []
        if self._contains_any_field_reference(
            where_clause,
            self._field_candidates(sql_context, sql_context.version_context.field),
        ):
            return []
        return ["sql is missing required version filter from sql context"]

    def _validate_month_filter_semantics(
        self,
        sql_context: SqlGenerationContext,
        where_clause: str,
    ) -> list[str]:
        if self.semantic_runtime is None:
            return []

        if any(item.field == "biz_date" for item in sql_context.filters):
            return []

        month_values = self._sql_context_month_values(sql_context)
        if not month_values:
            return []

        day_field_candidates = self._time_field_candidates(sql_context, "biz_date")
        if not day_field_candidates:
            return []

        for month_value in month_values:
            for candidate in day_field_candidates:
                candidate_format = candidate.get("format") or "YYYY-MM-DD"
                month_range = self.semantic_runtime.month_range_literals(month_value, candidate_format)
                if not month_range:
                    continue
                day_one_literal, _ = month_range
                for field_name in self._time_candidate_field_names(candidate):
                    if self._matches_single_literal_comparison(where_clause, field_name, day_one_literal):
                        return [
                            "sql collapses biz_month filter to a single day; expand it to a full-month range or month expression"
                        ]
        return []

    def _validate_time_literal_formats(
        self,
        sql_context: SqlGenerationContext,
        where_clause: str,
    ) -> list[str]:
        if self.semantic_runtime is None or not where_clause:
            return []

        invalid_literal_patterns = {
            "YYYYMMDD": [r"20\d{2}-\d{2}-\d{2}"],
            "YYYY-MM-DD": [r"20\d{8}"],
            "YYYYMM": [r"20\d{2}-\d{2}(?:-\d{2})?", r"20\d{6}"],
            "YYYY-MM": [r"20\d{4}(?:\d{2})?", r"20\d{2}-\d{2}-\d{2}"],
        }
        errors: list[str] = []
        inspected_fields: set[tuple[str, str]] = set()
        for logical_field in ["biz_date", "biz_month"]:
            for candidate in self._time_field_candidates(sql_context, logical_field):
                field_format = str(candidate.get("format") or "").strip().upper()
                if not field_format:
                    continue
                for field_name in self._time_candidate_field_names(candidate):
                    key = (field_name, field_format)
                    if key in inspected_fields:
                        continue
                    inspected_fields.add(key)
                    for literal_pattern in invalid_literal_patterns.get(field_format, []):
                        if self._matches_direct_literal_pattern(where_clause, field_name, literal_pattern):
                            errors.append(
                                f"sql compares {field_name} as {field_format} but uses incompatible time literals; rewrite literals to match the physical field format or use an equivalent time expression"
                            )
                            break
        return errors

    def _validate_limit_consistency(
        self,
        sql_context: SqlGenerationContext,
        sql_limit: int | None,
        has_limit: bool,
    ) -> list[str]:
        if not has_limit:
            return []
        if sql_limit is None:
            return []
        if sql_limit > sql_context.limit:
            return [f"sql limit {sql_limit} exceeds sql context limit {sql_context.limit}"]
        return []

    def _validate_selected_dimensions(
        self,
        sql_context: SqlGenerationContext,
        inspection,
    ) -> list[str]:
        if not sql_context.dimensions:
            return []
        select_fields = {field.lower() for field in inspection.select_fields}
        missing_dimensions = [
            field
            for field in sql_context.dimensions
            if not self._field_candidates(sql_context, field).intersection(select_fields)
        ]
        if missing_dimensions:
            return [
                "sql does not project required dimensions from sql context: "
                + ", ".join(sorted(set(missing_dimensions)))
            ]
        return []

    def _time_field_candidates(self, sql_context: SqlGenerationContext, logical_field: str) -> list[dict]:
        if self.semantic_runtime is None:
            return []
        return self.semantic_runtime.resolve_time_field_candidates(
            sql_context.subject_domain,
            sql_context.tables,
            logical_field,
        )

    def _time_candidate_field_names(self, candidate: dict) -> set[str]:
        names: set[str] = set()
        field_name = str(candidate.get("field", "")).strip().lower()
        qualified_field = str(candidate.get("qualified_field", "")).strip().lower()
        if field_name:
            names.add(field_name)
        if qualified_field:
            names.add(qualified_field)
        return names

    def _sql_context_month_values(self, sql_context: SqlGenerationContext) -> list[str]:
        if self.semantic_runtime is None:
            return []
        values: list[str] = []
        for item in sql_context.filters:
            if item.field != "biz_month":
                continue
            candidate_values = [item.value]
            if item.op == "between" and isinstance(item.value, list):
                candidate_values = list(item.value)
            for candidate_value in candidate_values:
                compact_month = self.semantic_runtime.compact_month_value(str(candidate_value))
                if compact_month and compact_month not in values:
                    values.append(compact_month)
        if values:
            return values
        time_context = sql_context.time_context
        if time_context.grain == "month" and time_context.range:
            for candidate_value in [time_context.range.start, time_context.range.end]:
                compact_month = self.semantic_runtime.compact_month_value(candidate_value)
                if compact_month and compact_month not in values:
                    values.append(compact_month)
        return values

    def _matches_single_literal_comparison(self, sql_fragment: str, field_name: str, literal: str) -> bool:
        single_day_pattern = rf"\b{re.escape(field_name)}\b\s*=\s*'{re.escape(literal)}'"
        between_same_day_pattern = (
            rf"\b{re.escape(field_name)}\b\s+between\s+'{re.escape(literal)}'\s+and\s+'{re.escape(literal)}'"
        )
        return re.search(single_day_pattern, sql_fragment, re.IGNORECASE) is not None or re.search(
            between_same_day_pattern,
            sql_fragment,
            re.IGNORECASE,
        ) is not None

    def _matches_direct_literal_pattern(self, sql_fragment: str, field_name: str, literal_pattern: str) -> bool:
        comparison_pattern = (
            rf"\b{re.escape(field_name)}\b\s*(?:=|>=|<=|>|<)\s*'(?:{literal_pattern})'"
        )
        between_pattern = (
            rf"\b{re.escape(field_name)}\b\s+between\s+'(?:{literal_pattern})'\s+and\s+'(?:{literal_pattern})'"
        )
        return re.search(comparison_pattern, sql_fragment, re.IGNORECASE) is not None or re.search(
            between_pattern,
            sql_fragment,
            re.IGNORECASE,
        ) is not None

    def _validate_unexpected_group_by_fields(
        self,
        sql_context: SqlGenerationContext,
        inspection,
    ) -> list[str]:
        if not sql_context.dimensions:
            return []
        if not any(
            function in self.ast_validator.AGGREGATE_FUNCTIONS
            for function in inspection.outer_functions
        ):
            return []
        actual_group_by_fields = {field.lower() for field in inspection.group_by_fields}
        if not actual_group_by_fields:
            return []
        allowed_group_by_fields: set[str] = set()
        for field in sql_context.dimensions:
            allowed_group_by_fields.update(self._field_candidates(sql_context, field))
        allowed_group_by_fields = {field.lower() for field in allowed_group_by_fields}
        unexpected = [
            field
            for field in actual_group_by_fields
            if field not in allowed_group_by_fields
        ]
        if unexpected:
            return [
                "sql groups by fields outside sql context dimensions: "
                + ", ".join(sorted(set(unexpected)))
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
            warnings.append(f"sql result limit {inspection.limit_value} is high; review result size governance")
        if not inspection.has_limit and not inspection.has_where:
            warnings.append("sql has neither WHERE nor result limit; high full-scan risk")
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
            if "sql quality" in lowered:
                flags.append("quality_risk")
            if "permission filters" in lowered:
                flags.append("permission_risk")
            if "sources outside sql context" in lowered or "unsupported fields" in lowered:
                flags.append("context_mismatch_risk")
        deduped: list[str] = []
        for flag in flags:
            if flag not in deduped:
                deduped.append(flag)
        return deduped

    def _risk_level_for_flags(self, risk_flags: list[str]) -> str:
        if any(flag in risk_flags for flag in ["permission_risk", "context_mismatch_risk", "scan_risk", "join_risk"]):
            return "high"
        if risk_flags:
            return "medium"
        return "low"
