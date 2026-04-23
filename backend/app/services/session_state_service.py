from __future__ import annotations

from copy import deepcopy

from backend.app.models.query_plan import QueryPlan
from backend.app.models.session_state import SessionState


class SessionStateService:
    def build_next_state(
        self,
        query_plan: QueryPlan,
        previous_state: SessionState | None,
        sql: str | None = None,
    ) -> SessionState:
        if previous_state is None or not query_plan.inherit_context:
            return self._new_state(query_plan, sql, previous_state)

        state = previous_state.model_copy(deep=True)
        state.topic = query_plan.subject_domain
        state.subject_domain = query_plan.subject_domain
        state.entities = (
            query_plan.context_delta.replace_entities
            or query_plan.entities
            or state.entities
        )
        state.tables = query_plan.tables or state.tables
        state.semantic_views = query_plan.semantic_views or state.semantic_views
        state.metrics = (
            query_plan.context_delta.replace_metrics or query_plan.metrics or state.metrics
        )
        state.dimensions = (
            query_plan.context_delta.replace_dimensions
            or query_plan.dimensions
            or state.dimensions
        )
        current_filters = [] if query_plan.context_delta.clear_filters else state.filters
        state.filters = self._merge_filters(
            current_filters,
            query_plan.context_delta.add_filters,
            remove_fields=query_plan.context_delta.remove_filters,
        )
        state.sort = query_plan.context_delta.replace_sort or query_plan.sort or state.sort
        state.limit = query_plan.context_delta.replace_limit or query_plan.limit or state.limit
        if query_plan.context_delta.replace_time_context.grain != "unknown":
            state.time_context = query_plan.context_delta.replace_time_context
        elif query_plan.time_context.grain != "unknown":
            state.time_context = query_plan.time_context
        state.version_context = (
            query_plan.context_delta.replace_version_context
            or query_plan.version_context
            or state.version_context
        )
        state.last_question_type = query_plan.question_type
        state.last_query_plan = query_plan
        state.last_sql = sql
        state.last_result_shape = self._result_shape(query_plan)
        return state

    def _new_state(
        self,
        query_plan: QueryPlan,
        sql: str | None,
        previous_state: SessionState | None,
    ) -> SessionState:
        session_id = previous_state.session_id if previous_state else "session_pending"
        return SessionState(
            session_id=session_id,
            topic=query_plan.subject_domain,
            subject_domain=query_plan.subject_domain,
            entities=query_plan.entities,
            tables=query_plan.tables,
            semantic_views=query_plan.semantic_views,
            metrics=query_plan.metrics,
            dimensions=query_plan.dimensions,
            filters=deepcopy(query_plan.filters),
            sort=deepcopy(query_plan.sort),
            limit=query_plan.limit,
            time_context=query_plan.time_context,
            version_context=query_plan.version_context,
            last_question_type=query_plan.question_type,
            last_query_plan=query_plan,
            last_sql=sql,
            last_result_shape=self._result_shape(query_plan),
        )

    def _merge_filters(self, current_filters, new_filters, remove_fields=None):
        remove_fields = set(remove_fields or [])
        merged = {
            self._filter_key(item): item
            for item in current_filters
            if item.field not in remove_fields
        }
        for item in new_filters:
            merged[self._filter_key(item)] = item
        return list(merged.values())

    def _filter_key(self, filter_item) -> str:
        return f"{filter_item.field}:{filter_item.op}"

    def _result_shape(self, query_plan: QueryPlan) -> str:
        if query_plan.dimensions:
            return "_by_".join(query_plan.dimensions)
        if query_plan.metrics:
            return "metric_only"
        return "unknown"
