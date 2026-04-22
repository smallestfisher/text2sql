from __future__ import annotations

from backend.app.models.auth import UserContext
from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.services.policy_engine import PolicyEngine


class PermissionService:
    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()

    def apply_to_query_plan(
        self,
        query_plan: QueryPlan,
        user_context: UserContext | None,
    ) -> tuple[QueryPlan, list[str]]:
        warnings: list[str] = []
        decision = self.policy_engine.evaluate(user_context, query_plan=query_plan)
        if user_context is None:
            return query_plan, warnings

        if not decision.allow_execute:
            query_plan.need_clarification = True
            query_plan.reason = "当前用户没有执行 SQL 的权限。"
            warnings.append("user is not allowed to execute SQL")
            return query_plan, warnings

        permission_filters = decision.filters
        if permission_filters:
            existing = {self._filter_key(item) for item in query_plan.filters}
            existing_fields = {item.field for item in query_plan.filters}
            for filter_item in permission_filters:
                if (
                    self._filter_key(filter_item) not in existing
                    and filter_item.field not in existing_fields
                ):
                    query_plan.filters.append(filter_item)
                    existing.add(self._filter_key(filter_item))
                    existing_fields.add(filter_item.field)
            warnings.append("data scope filters injected into query plan")

        return query_plan, warnings

    def can_view_sql(self, user_context: UserContext | None) -> bool:
        return self.policy_engine.evaluate(user_context).allow_view_sql

    def required_filter_fields(
        self,
        query_plan: QueryPlan,
        user_context: UserContext | None,
    ) -> list[str]:
        decision = self.policy_engine.evaluate(user_context, query_plan=query_plan)
        fields: list[str] = []
        for filter_item in decision.filters:
            if filter_item.field not in fields:
                fields.append(filter_item.field)
        return fields

    def _filter_key(self, filter_item: FilterItem) -> str:
        return f"{filter_item.field}:{filter_item.op}"
