from __future__ import annotations

from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryPlanCompiler:
    def __init__(self, semantic_runtime: SemanticRuntime, default_limit: int = 200) -> None:
        self.semantic_runtime = semantic_runtime
        self.default_limit = default_limit

    def compile(self, query_plan: QueryPlan, retrieval: RetrievalContext | None = None) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)

        if not compiled.semantic_views and compiled.subject_domain != "unknown":
            compiled.semantic_views = self.semantic_runtime.rank_semantic_views(
                domain_name=compiled.subject_domain,
                metrics=compiled.metrics,
                dimensions=compiled.dimensions,
                filters=compiled.filters,
                sort_fields=[item.field for item in compiled.sort],
                version_field=compiled.version_context.field if compiled.version_context else None,
            )
        elif compiled.semantic_views:
            compiled.semantic_views = self.semantic_runtime.rank_semantic_views(
                domain_name=compiled.subject_domain,
                metrics=compiled.metrics,
                dimensions=compiled.dimensions,
                filters=compiled.filters,
                sort_fields=[item.field for item in compiled.sort],
                version_field=compiled.version_context.field if compiled.version_context else None,
            )

        if not compiled.tables and compiled.metrics:
            tables: list[str] = []
            for metric in compiled.metrics:
                for table in self.semantic_runtime.metric_tables(metric):
                    if table not in tables:
                        tables.append(table)
            compiled.tables = tables

        compiled.join_path = self.semantic_runtime.resolve_join_path(compiled.tables)

        if compiled.limit <= 0:
            compiled.limit = self.default_limit

        if retrieval and retrieval.semantic_views and not compiled.semantic_views:
            compiled.semantic_views = retrieval.semantic_views

        compiled = self.semantic_runtime.apply_domain_constraints(compiled)

        return compiled

    def apply_llm_hint(self, query_plan: QueryPlan, llm_hint: dict | None) -> QueryPlan:
        if not llm_hint:
            return query_plan

        compiled = query_plan.model_copy(deep=True)
        for field_name in ("subject_domain", "tables", "semantic_views", "metrics", "dimensions", "join_path", "reason"):
            value = llm_hint.get(field_name)
            if value:
                setattr(compiled, field_name, value)

        llm_filters = llm_hint.get("filters")
        if isinstance(llm_filters, list) and llm_filters:
            compiled.filters = [
                item if isinstance(item, FilterItem) else FilterItem(**item)
                for item in llm_filters
                if isinstance(item, (dict, FilterItem))
            ]

        return compiled
