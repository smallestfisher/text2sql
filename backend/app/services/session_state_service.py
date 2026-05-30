from __future__ import annotations

from copy import deepcopy

from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.models.session_state import QueryTurnRecord, SessionState


class SessionStateService:
    def build_next_state(
        self,
        query_plan: QueryPlan,
        previous_state: SessionState | None,
        question: str | None = None,
        effective_question: str | None = None,
        sql: str | None = None,
    ) -> SessionState:
        if previous_state is None or not query_plan.inherit_context:
            return self._new_state(query_plan, sql, previous_state, question, effective_question)

        state = previous_state.model_copy(deep=True)
        state.topic = query_plan.subject_domain
        state.subject_domain = query_plan.subject_domain
        state.entities = (
            query_plan.context_delta.replace_entities
            or query_plan.entities
            or state.entities
        )
        state.tables = query_plan.tables or state.tables
        if query_plan.analysis_mode == "detail" and not query_plan.metrics:
            state.metrics = []
        else:
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
        state.analysis_mode = (
            query_plan.context_delta.replace_analysis_mode
            or query_plan.analysis_mode
            or state.analysis_mode
        )
        state.last_question_type = query_plan.question_type
        state.last_query_plan = query_plan
        state.last_sql = sql
        state.last_result_shape = self._result_shape(query_plan)
        state.last_semantic_brief = query_plan.semantic_brief
        state.last_effective_question = effective_question or question
        state.recent_turns = self._append_turn(previous_state, query_plan, question, effective_question)
        state.conversation_summary = self._conversation_summary(state.recent_turns)
        if not query_plan.need_clarification:
            state.pending_clarification = None
        return state

    def _new_state(
        self,
        query_plan: QueryPlan,
        sql: str | None,
        previous_state: SessionState | None,
        question: str | None,
        effective_question: str | None,
    ) -> SessionState:
        session_id = previous_state.session_id if previous_state else "session_pending"
        state = SessionState(
            session_id=session_id,
            topic=query_plan.subject_domain,
            subject_domain=query_plan.subject_domain,
            entities=query_plan.entities,
            tables=query_plan.tables,
            metrics=query_plan.metrics,
            dimensions=query_plan.dimensions,
            filters=deepcopy(query_plan.filters),
            sort=deepcopy(query_plan.sort),
            limit=query_plan.limit,
            time_context=query_plan.time_context,
            version_context=query_plan.version_context,
            analysis_mode=query_plan.analysis_mode,
            last_question_type=query_plan.question_type,
            last_query_plan=query_plan,
            last_sql=sql,
            last_result_shape=self._result_shape(query_plan),
            last_semantic_brief=query_plan.semantic_brief,
            last_effective_question=effective_question or question,
            recent_turns=self._append_turn(previous_state, query_plan, question, effective_question),
            pending_clarification=None,
        )
        state.conversation_summary = self._conversation_summary(state.recent_turns)
        return state

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

    def _append_turn(
        self,
        previous_state: SessionState | None,
        query_plan: QueryPlan,
        question: str | None,
        effective_question: str | None = None,
    ) -> list[QueryTurnRecord]:
        turns = list(previous_state.recent_turns if previous_state else [])
        turns.append(
            QueryTurnRecord(
                question=question,
                effective_question=effective_question or question,
                summary=self._query_summary(query_plan),
                semantic_brief=query_plan.semantic_brief,
            )
        )
        return turns[-4:]

    def _conversation_summary(self, turns: list[QueryTurnRecord]) -> str:
        lines: list[str] = []
        for turn in turns[-4:]:
            parts: list[str] = []
            if turn.question:
                parts.append(f"用户：{turn.question}")
            if turn.effective_question and turn.effective_question != turn.question:
                parts.append(f"改写后：{turn.effective_question}")
            if turn.semantic_brief:
                parts.append(f"摘要：{turn.semantic_brief}")
            elif turn.summary:
                parts.append(f"摘要：{turn.summary}")
            if parts:
                lines.append("；".join(parts))
        return "\n".join(lines)

    def _query_summary(self, query_plan: QueryPlan) -> str:
        parts = [query_plan.subject_domain]
        if query_plan.metrics:
            parts.append("metrics=" + ",".join(query_plan.metrics))
        if query_plan.dimensions:
            parts.append("by=" + ",".join(query_plan.dimensions))
        filter_text = self._filter_summary(query_plan.filters)
        if filter_text:
            parts.append("filters=" + filter_text)
        return " | ".join(parts)

    def _filter_summary(self, filters: list[FilterItem]) -> str:
        values = []
        for item in filters[:8]:
            values.append(f"{item.field}{item.op}{item.value}")
        return ",".join(values)
