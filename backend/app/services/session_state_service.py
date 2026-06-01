from __future__ import annotations

from copy import deepcopy

from backend.app.models.context_summary import ContextSummary
from backend.app.models.semantic_types import FilterItem
from backend.app.models.session_state import QueryTurnRecord, SessionState, SessionStateUpdate
from backend.app.models.sql_generation_context import SqlGenerationContext


class SessionStateService:
    def build_next_state(
        self,
        update: SessionStateUpdate,
        previous_state: SessionState | None,
        question: str | None = None,
        effective_question: str | None = None,
        sql: str | None = None,
    ) -> SessionState:
        if previous_state is None or not update.inherit_context:
            return self._new_state(update, sql, previous_state, question, effective_question)

        state = previous_state.model_copy(deep=True)
        state.topic = update.subject_domain
        state.subject_domain = update.subject_domain
        state.entities = (
            update.context_delta.replace_entities
            or update.entities
            or state.entities
        )
        state.tables = update.tables or state.tables
        if update.analysis_mode == "detail" and not update.metrics:
            state.metrics = []
        else:
            state.metrics = (
                update.context_delta.replace_metrics or update.metrics or state.metrics
            )
        state.dimensions = (
            update.context_delta.replace_dimensions
            or update.dimensions
            or state.dimensions
        )
        current_filters = [] if update.context_delta.clear_filters else state.filters
        state.filters = self._merge_filters(
            current_filters,
            update.context_delta.add_filters,
            remove_fields=update.context_delta.remove_filters,
        )
        state.sort = update.context_delta.replace_sort or update.sort or state.sort
        state.limit = update.context_delta.replace_limit or update.limit or state.limit
        if update.context_delta.replace_time_context.grain != "unknown":
            state.time_context = update.context_delta.replace_time_context
        elif update.time_context.grain != "unknown":
            state.time_context = update.time_context
        state.version_context = (
            update.context_delta.replace_version_context
            or update.version_context
            or state.version_context
        )
        state.analysis_mode = (
            update.context_delta.replace_analysis_mode
            or update.analysis_mode
            or state.analysis_mode
        )
        state.last_question_type = update.question_type
        state.last_context_summary = self._context_summary(update)
        state.last_sql = sql
        state.last_result_shape = self._result_shape(update)
        state.last_semantic_brief = update.semantic_brief
        state.last_effective_question = effective_question or question
        state.recent_turns = self._append_turn(previous_state, update, question, effective_question)
        state.conversation_summary = self._conversation_summary(state.recent_turns)
        if not update.need_clarification:
            state.pending_clarification = None
        return state

    def _new_state(
        self,
        update: SessionStateUpdate,
        sql: str | None,
        previous_state: SessionState | None,
        question: str | None,
        effective_question: str | None,
    ) -> SessionState:
        session_id = previous_state.session_id if previous_state else "session_pending"
        state = SessionState(
            session_id=session_id,
            topic=update.subject_domain,
            subject_domain=update.subject_domain,
            entities=update.entities,
            tables=update.tables,
            metrics=update.metrics,
            dimensions=update.dimensions,
            filters=deepcopy(update.filters),
            sort=deepcopy(update.sort),
            limit=update.limit,
            time_context=update.time_context,
            version_context=update.version_context,
            analysis_mode=update.analysis_mode,
            last_question_type=update.question_type,
            last_context_summary=self._context_summary(update),
            last_sql=sql,
            last_result_shape=self._result_shape(update),
            last_semantic_brief=update.semantic_brief,
            last_effective_question=effective_question or question,
            recent_turns=self._append_turn(previous_state, update, question, effective_question),
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

    def _result_shape(self, update: SessionStateUpdate) -> str:
        if update.dimensions:
            return "_by_".join(update.dimensions)
        if update.metrics:
            return "metric_only"
        return "unknown"

    def _context_summary(self, update: SessionStateUpdate) -> ContextSummary:
        return ContextSummary(
            question_type=update.question_type,
            subject_domain=update.subject_domain,
            semantic_brief=update.semantic_brief,
            tables=list(update.tables),
            limit=update.limit,
            need_clarification=update.need_clarification,
            clarification_question=update.clarification_question,
            source="session_state",
        )

    def _append_turn(
        self,
        previous_state: SessionState | None,
        update: SessionStateUpdate,
        question: str | None,
        effective_question: str | None = None,
    ) -> list[QueryTurnRecord]:
        turns = list(previous_state.recent_turns if previous_state else [])
        turns.append(
            QueryTurnRecord(
                question=question,
                effective_question=effective_question or question,
                summary=self._query_summary(update),
                semantic_brief=update.semantic_brief,
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

    def _query_summary(self, update: SessionStateUpdate) -> str:
        parts = [update.subject_domain]
        if update.metrics:
            parts.append("metrics=" + ",".join(update.metrics))
        if update.dimensions:
            parts.append("by=" + ",".join(update.dimensions))
        filter_text = self._filter_summary(update.filters)
        if filter_text:
            parts.append("filters=" + filter_text)
        return " | ".join(parts)

    def _filter_summary(self, filters: list[FilterItem]) -> str:
        values = []
        for item in filters[:8]:
            values.append(f"{item.field}{item.op}{item.value}")
        return ",".join(values)

    @staticmethod
    def update_from_sql_context(sql_context: SqlGenerationContext) -> SessionStateUpdate:
        return SessionStateUpdate(
            question_type=sql_context.question_type,
            subject_domain=sql_context.subject_domain,
            entities=list(sql_context.entities),
            tables=list(sql_context.tables),
            metrics=list(sql_context.metrics),
            dimensions=list(sql_context.dimensions),
            filters=list(sql_context.filters),
            sort=list(sql_context.sort),
            limit=sql_context.limit,
            time_context=sql_context.time_context,
            version_context=sql_context.version_context,
            analysis_mode=sql_context.analysis_mode,
            inherit_context=sql_context.inherit_context,
            context_delta=sql_context.context_delta,
            need_clarification=sql_context.need_clarification,
            clarification_question=sql_context.clarification_question,
            semantic_brief=sql_context.semantic_brief,
        )
