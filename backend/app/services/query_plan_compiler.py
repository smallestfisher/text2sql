from __future__ import annotations

from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import SortItem
from backend.app.models.query_plan import VersionContext
from backend.app.models.retrieval import RetrievalContext
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryPlanCompiler:
    def __init__(self, semantic_runtime: SemanticRuntime, default_limit: int = 200) -> None:
        self.semantic_runtime = semantic_runtime
        self.default_limit = default_limit

    def compile(self, query_plan: QueryPlan, retrieval: RetrievalContext | None = None) -> QueryPlan:
        fallback_semantic_views = retrieval.semantic_views if retrieval else None
        return self.semantic_runtime.sanitize_query_plan(
            query_plan=query_plan,
            fallback_semantic_views=fallback_semantic_views,
            default_limit=self.default_limit,
        )

    def apply_llm_hint(self, query_plan: QueryPlan, llm_hint: dict | None) -> QueryPlan:
        if not llm_hint:
            return query_plan

        compiled = query_plan.model_copy(deep=True)
        subject_domain = llm_hint.get("subject_domain")
        if isinstance(subject_domain, str) and self.semantic_runtime.is_known_domain(subject_domain):
            compiled.subject_domain = subject_domain

        tables = llm_hint.get("tables")
        if isinstance(tables, list):
            allowed_domain_tables = set(self.semantic_runtime.domain_tables(compiled.subject_domain))
            compiled.tables = [
                table
                for table in tables
                if isinstance(table, str)
                and self.semantic_runtime.is_known_table(table)
                and (not allowed_domain_tables or table in allowed_domain_tables)
            ]

        semantic_views = llm_hint.get("semantic_views")
        if isinstance(semantic_views, list):
            ranked_views = self.semantic_runtime.semantic_views_for_domain(compiled.subject_domain)
            allowed_views = set(ranked_views)
            compiled.semantic_views = [
                view_name
                for view_name in semantic_views
                if isinstance(view_name, str)
                and self.semantic_runtime.is_known_view(view_name)
                and (not allowed_views or view_name in allowed_views)
            ]

        metrics = llm_hint.get("metrics")
        if isinstance(metrics, list):
            compiled.metrics = [
                metric
                for metric in metrics
                if isinstance(metric, str) and self.semantic_runtime.is_known_metric(metric)
            ]
        dimensions = llm_hint.get("dimensions")
        if isinstance(dimensions, list):
            compiled.dimensions = [item for item in dimensions if isinstance(item, str)]

        join_path = llm_hint.get("join_path")
        if isinstance(join_path, list):
            compiled.join_path = [item for item in join_path if isinstance(item, str)]

        reason = llm_hint.get("reason")
        if isinstance(reason, str) and reason.strip():
            compiled.reason = reason

        llm_version_context = llm_hint.get("version_context")
        if isinstance(llm_version_context, dict):
            field = llm_version_context.get("field")
            value = llm_version_context.get("value")
            if isinstance(value, str):
                compiled.version_context = VersionContext(
                    field=field if isinstance(field, str) else None,
                    value=value,
                )

        llm_filters = llm_hint.get("filters")
        if isinstance(llm_filters, list) and llm_filters:
            compiled.filters = self._coerce_filters(llm_filters)

        llm_sort = llm_hint.get("sort")
        if isinstance(llm_sort, list):
            compiled.sort = self._coerce_sort(llm_sort)

        llm_limit = llm_hint.get("limit")
        if isinstance(llm_limit, int):
            compiled.limit = llm_limit

        return self.semantic_runtime.sanitize_query_plan(
            query_plan=compiled,
            default_limit=self.default_limit,
        )

    def _coerce_filters(self, raw_filters: list[dict | FilterItem]) -> list[FilterItem]:
        filters: list[FilterItem] = []
        for item in raw_filters:
            try:
                filters.append(item if isinstance(item, FilterItem) else FilterItem(**item))
            except Exception:
                continue
        return filters

    def _coerce_sort(self, raw_sort: list[dict | SortItem]) -> list[SortItem]:
        sort_items: list[SortItem] = []
        for item in raw_sort:
            try:
                sort_items.append(item if isinstance(item, SortItem) else SortItem(**item))
            except Exception:
                continue
        return sort_items
