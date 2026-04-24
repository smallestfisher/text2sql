from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.models.query_plan import QueryPlan
from backend.app.services.semantic_runtime import SemanticRuntime


@dataclass
class QueryPlanValidationResult:
    errors: list[str]
    warnings: list[str]
    risk_level: str = "low"
    risk_flags: list[str] = field(default_factory=list)


class QueryPlanValidator:
    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime

    def validate(self, query_plan: QueryPlan, semantic_layer: dict) -> tuple[list[str], list[str]]:
        result = self.validate_detailed(query_plan=query_plan, semantic_layer=semantic_layer)
        return result.errors, result.warnings

    def validate_detailed(
        self,
        query_plan: QueryPlan,
        semantic_layer: dict,
    ) -> QueryPlanValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        domain_names = {item["name"] for item in semantic_layer.get("domains", [])}
        metric_names = {item["name"] for item in semantic_layer.get("metrics", [])}
        graph_nodes = set(semantic_layer.get("semantic_graph", {}).get("nodes", []))
        semantic_views = {
            item["name"] for item in semantic_layer.get("semantic_views", [])
        }
        entity_names = {item["name"] for item in semantic_layer.get("entities", [])}

        if query_plan.subject_domain not in domain_names and query_plan.subject_domain != "unknown":
            errors.append(f"unknown subject domain: {query_plan.subject_domain}")

        unknown_tables = [table for table in query_plan.tables if table not in graph_nodes]
        if unknown_tables:
            errors.append(f"unknown tables: {', '.join(unknown_tables)}")

        unknown_views = [
            view for view in query_plan.semantic_views if view not in semantic_views
        ]
        if unknown_views:
            errors.append(f"unknown semantic views: {', '.join(unknown_views)}")

        unknown_metrics = [
            metric for metric in query_plan.metrics if metric not in metric_names
        ]
        if unknown_metrics:
            errors.append(f"unknown metrics: {', '.join(unknown_metrics)}")

        unknown_entities = [
            entity for entity in query_plan.entities if entity not in entity_names
        ]
        if unknown_entities:
            errors.append(f"unknown entities: {', '.join(unknown_entities)}")

        if not query_plan.need_clarification:
            if not query_plan.metrics:
                warnings.append("query plan does not include metrics")
            if not query_plan.tables and not query_plan.semantic_views:
                errors.append("query plan must include at least one table or semantic view")

        if query_plan.need_clarification and not query_plan.clarification_question:
            warnings.append("clarification is required but no clarification question was provided")

        if query_plan.question_type == "follow_up":
            if not query_plan.inherit_context:
                errors.append("follow-up query plan must inherit context")
            elif not self._context_delta_has_updates(query_plan):
                warnings.append("follow-up query plan does not include explicit context delta updates")

        if self.semantic_runtime is not None and query_plan.tables and not query_plan.semantic_views:
            expected_join_path = self.semantic_runtime.resolve_join_path(query_plan.tables)
            if expected_join_path and not query_plan.join_path:
                warnings.append("query plan join path is empty; semantic runtime can provide a path")

        if self.semantic_runtime is not None and query_plan.semantic_views:
            allowed_fields = self.semantic_runtime.allowed_fields_for_plan(query_plan)
            if allowed_fields:
                unsupported_metrics = [
                    metric
                    for metric in query_plan.metrics
                    if self.semantic_runtime.metric_column(metric) not in allowed_fields
                ]
                if unsupported_metrics:
                    errors.append(
                        f"query plan metrics are not supported by selected semantic views: {', '.join(unsupported_metrics)}"
                    )

                unknown_dimensions = [
                    field for field in query_plan.dimensions if field not in allowed_fields
                ]
                if unknown_dimensions:
                    errors.append(
                        f"query plan references unsupported dimensions: {', '.join(unknown_dimensions)}"
                    )

                unknown_filter_fields = [
                    item.field for item in query_plan.filters if item.field not in allowed_fields
                ]
                if unknown_filter_fields:
                    errors.append(
                        "query plan references unsupported filter fields: "
                        + ", ".join(sorted(set(unknown_filter_fields)))
                    )

                unknown_sort_fields = [
                    item.field for item in query_plan.sort if item.field not in allowed_fields
                ]
                if unknown_sort_fields:
                    errors.append(
                        f"query plan references unsupported sort fields: {', '.join(sorted(set(unknown_sort_fields)))}"
                    )

                if (
                    query_plan.version_context is not None
                    and query_plan.version_context.field
                    and query_plan.version_context.field not in allowed_fields
                ):
                    errors.append(
                        f"query plan references unsupported version field: {query_plan.version_context.field}"
                    )

        if (
            self.semantic_runtime is not None
            and self.semantic_runtime.warn_if_missing_time_filter(query_plan.subject_domain)
        ):
            time_fields = set(self.semantic_runtime.time_filter_fields(query_plan.subject_domain))
            filter_fields = {item.field for item in query_plan.filters}
            if time_fields and not time_fields.intersection(filter_fields):
                warnings.append("query plan does not include a time filter; this may cause wide scans")

        if self.semantic_runtime is not None:
            version_field = self.semantic_runtime.query_profile(query_plan.subject_domain).get("version_field")
            filter_fields = {item.field for item in query_plan.filters}
            if (
                query_plan.subject_domain == "demand"
                and not query_plan.need_clarification
                and "demand_qty" in query_plan.metrics
                and version_field
                and query_plan.version_context is None
                and version_field not in filter_fields
            ):
                warnings.append(
                    "demand query plan does not include a version filter; verify whether V/P version is required"
                )

        if len(query_plan.metrics) > 1 and not query_plan.dimensions and not query_plan.filters:
            warnings.append("multi-metric query plan has no dimensions or filters; verify aggregation scope")

        risk_flags = self._collect_risk_flags(query_plan, errors, warnings)
        return QueryPlanValidationResult(
            errors=errors,
            warnings=warnings,
            risk_level=self._risk_level_for_flags(risk_flags),
            risk_flags=risk_flags,
        )

    def _context_delta_has_updates(self, query_plan: QueryPlan) -> bool:
        context_delta = query_plan.context_delta
        return bool(
            context_delta.add_filters
            or context_delta.remove_filters
            or context_delta.clear_filters
            or context_delta.replace_entities
            or context_delta.replace_metrics
            or context_delta.replace_dimensions
            or context_delta.replace_sort
            or context_delta.replace_version_context is not None
            or context_delta.replace_limit is not None
            or context_delta.replace_analysis_mode is not None
            or context_delta.replace_time_context.grain != "unknown"
        )

    def _collect_risk_flags(
        self,
        query_plan: QueryPlan,
        errors: list[str],
        warnings: list[str],
    ) -> list[str]:
        flags: list[str] = []
        if query_plan.need_clarification:
            flags.append("clarification_risk")
        if query_plan.question_type == "follow_up" and query_plan.inherit_context:
            flags.append("context_inheritance_risk")
        if len(query_plan.metrics) > 1 and not query_plan.dimensions and not query_plan.filters:
            flags.append("aggregation_scope_risk")
        for message in errors + warnings:
            lowered = message.lower()
            if "unknown" in lowered or "unsupported" in lowered:
                flags.append("semantic_contract_risk")
            if "time filter" in lowered:
                flags.append("scan_risk")
            if "version filter" in lowered:
                flags.append("version_scope_risk")
            if "join path" in lowered:
                flags.append("join_risk")
        deduped: list[str] = []
        for flag in flags:
            if flag not in deduped:
                deduped.append(flag)
        return deduped

    def _risk_level_for_flags(self, risk_flags: list[str]) -> str:
        if any(
            flag in risk_flags
            for flag in [
                "semantic_contract_risk",
                "scan_risk",
                "join_risk",
                "version_scope_risk",
            ]
        ):
            return "high"
        if risk_flags:
            return "medium"
        return "low"
