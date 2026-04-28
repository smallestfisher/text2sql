from __future__ import annotations

from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryPlanCompiler:
    def __init__(self, semantic_runtime: SemanticRuntime, default_limit: int = 200) -> None:
        self.semantic_runtime = semantic_runtime
        self.default_limit = default_limit

    def compile(self, query_plan: QueryPlan, retrieval: RetrievalContext | None = None) -> QueryPlan:
        return self.semantic_runtime.sanitize_query_plan(
            query_plan=query_plan,
            default_limit=self.default_limit,
        )
