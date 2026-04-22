from __future__ import annotations

from backend.app.models.auth import UserContext
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import FilterItem
from backend.app.services.semantic_runtime import SemanticRuntime


class PolicyDecision:
    def __init__(
        self,
        allow_execute: bool,
        allow_view_sql: bool,
        filters: list[FilterItem] | None = None,
        reasons: list[str] | None = None,
    ) -> None:
        self.allow_execute = allow_execute
        self.allow_view_sql = allow_view_sql
        self.filters = filters or []
        self.reasons = reasons or []


class PolicyEngine:
    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime

    def evaluate(
        self,
        user_context: UserContext | None,
        query_plan: QueryPlan | None = None,
    ) -> PolicyDecision:
        if user_context is None:
            return PolicyDecision(allow_execute=True, allow_view_sql=True)

        filters: list[FilterItem] = []
        for scope_name, fallback_field in self._scope_definitions().items():
            scope_values = list(getattr(user_context.data_scope, scope_name, []))
            if not scope_values:
                continue
            field = self._scope_field(query_plan, scope_name, fallback_field)
            if field:
                filters.append(FilterItem(field=field, op="in", value=scope_values))

        reasons: list[str] = []
        if not user_context.can_execute_sql:
            reasons.append("execution is disabled by policy")
        if not user_context.can_view_sql:
            reasons.append("sql visibility is disabled by policy")

        return PolicyDecision(
            allow_execute=user_context.can_execute_sql,
            allow_view_sql=user_context.can_view_sql,
            filters=filters,
            reasons=reasons,
        )

    def _scope_field(
        self,
        query_plan: QueryPlan | None,
        scope_name: str,
        fallback: str,
    ) -> str:
        if self.semantic_runtime is None or query_plan is None:
            return fallback
        profile = self.semantic_runtime.query_profile(query_plan.subject_domain)
        return profile.get("permission_scope_fields", {}).get(scope_name, fallback)

    def _scope_definitions(self) -> dict[str, str]:
        return {
            "factories": "factory",
            "sbus": "sbu",
            "bus": "bu",
            "customers": "customer",
            "products": "product_ID",
        }
