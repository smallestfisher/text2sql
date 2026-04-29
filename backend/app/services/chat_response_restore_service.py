from __future__ import annotations

from backend.app.models.admin import RuntimeQueryLogRecord, RuntimeSqlAuditRecord
from backend.app.models.answer import AnswerPayload, normalize_answer_status
from backend.app.models.api import ChatResponse, ExecutionResponse, ValidationResponse
from backend.app.models.auth import UserContext
from backend.app.models.classification import QuestionClassification, QueryIntent
from backend.app.models.conversation import ChatMessage
from backend.app.models.query_plan import QueryPlan, TimeContext
from backend.app.models.retrieval import RetrievalContext
from backend.app.models.session_state import SessionState
from backend.app.models.trace import TraceRecord


class ChatResponseRestoreService:
    def __init__(
        self,
        *,
        audit_service,
        runtime_log_repository,
    ) -> None:
        self.audit_service = audit_service
        self.runtime_log_repository = runtime_log_repository

    def build_from_trace_id(
        self,
        trace_id: str,
        *,
        session_state: SessionState | None = None,
        messages: list[ChatMessage] | None = None,
        user_context: UserContext | None = None,
        trace: TraceRecord | None = None,
        query_log: RuntimeQueryLogRecord | None = None,
        sql_audit: RuntimeSqlAuditRecord | None = None,
    ) -> ChatResponse | None:
        trace = trace or self.audit_service.get_trace(trace_id)
        query_log = query_log or self.runtime_log_repository.get_query_log(trace_id)
        if trace is None or query_log is None:
            return None
        sql_audit = sql_audit or self.runtime_log_repository.get_sql_audit(trace_id)

        snapshot_payload = self._response_snapshot_payload(trace)
        if snapshot_payload is not None:
            response = self._restore_from_snapshot(
                snapshot_payload,
                trace=trace,
                session_state=session_state,
                sql_audit=sql_audit,
            )
            return response

        response = self._restore_from_artifacts(
            trace=trace,
            query_log=query_log,
            sql_audit=sql_audit,
            session_state=session_state,
            messages=messages or [],
        )
        return response

    def _restore_from_snapshot(
        self,
        snapshot_payload: dict,
        *,
        trace: TraceRecord,
        session_state: SessionState | None,
        sql_audit: RuntimeSqlAuditRecord | None,
    ) -> ChatResponse:
        payload = dict(snapshot_payload)
        if "query_intent" not in payload and "semantic_parse" in payload:
            payload["query_intent"] = payload.pop("semantic_parse")
        payload["trace"] = trace
        payload["sql"] = sql_audit.sql_text if sql_audit is not None else None
        self._normalize_legacy_answer_payload(payload)
        return ChatResponse(**payload)

    def _restore_from_artifacts(
        self,
        *,
        trace: TraceRecord,
        query_log: RuntimeQueryLogRecord,
        sql_audit: RuntimeSqlAuditRecord | None,
        session_state: SessionState | None,
        messages: list[ChatMessage],
    ) -> ChatResponse:
        plan_metadata = self._step_metadata(trace, "plan")
        compile_metadata = self._step_metadata(trace, "compile_plan")
        validate_plan_metadata = self._step_metadata(trace, "validate_plan")
        retrieve_metadata = self._step_metadata(trace, "retrieve")
        execute_metadata = self._step_metadata(trace, "execute")

        classification_payload = plan_metadata.get("classification") or {}
        query_intent_payload = plan_metadata.get("query_intent") or plan_metadata.get("semantic_parse") or {}
        compiled_plan_payload = compile_metadata.get("compiled_plan") or {}

        restored_state = session_state or SessionState(session_id=query_log.session_id or "session_pending")

        classification = QuestionClassification(**{
            "question_type": classification_payload.get("question_type", query_log.question_type or "new"),
            "subject_domain": classification_payload.get("subject_domain", query_log.subject_domain or restored_state.subject_domain),
            "inherit_context": classification_payload.get("inherit_context", False),
            "need_clarification": classification_payload.get("need_clarification", False),
            "reason": classification_payload.get("reason"),
            "reason_code": classification_payload.get("reason_code"),
            "suggested_reply": classification_payload.get("suggested_reply"),
            "clarification_question": classification_payload.get("clarification_question"),
            "context_delta": classification_payload.get("context_delta", {}),
            "confidence": classification_payload.get("confidence", 0.0),
        })
        query_intent = QueryIntent(**{
            "normalized_question": query_intent_payload.get("normalized_question", query_log.question or ""),
            "matched_metrics": query_intent_payload.get("matched_metrics", []),
            "matched_entities": query_intent_payload.get("matched_entities", []),
            "requested_dimensions": query_intent_payload.get("requested_dimensions", []),
            "filters": query_intent_payload.get("filters", []),
            "time_context": query_intent_payload.get("time_context", {}),
            "version_context": query_intent_payload.get("version_context"),
            "requested_sort": query_intent_payload.get("requested_sort", []),
            "requested_limit": query_intent_payload.get("requested_limit"),
            "analysis_mode": query_intent_payload.get("analysis_mode"),
            "subject_domain": query_intent_payload.get("subject_domain", classification.subject_domain),
            "has_follow_up_cue": query_intent_payload.get("has_follow_up_cue", False),
            "has_explicit_slots": query_intent_payload.get("has_explicit_slots", False),
        })

        query_plan_source = (
            restored_state.last_query_plan.model_dump(mode="json")
            if restored_state.last_query_plan is not None
            else compiled_plan_payload
        )
        query_plan = QueryPlan(**{
            "question_type": query_plan_source.get("question_type", classification.question_type),
            "subject_domain": query_plan_source.get("subject_domain", classification.subject_domain),
            "tables": query_plan_source.get("tables", []),
            "entities": query_plan_source.get("entities", []),
            "metrics": query_plan_source.get("metrics", []),
            "dimensions": query_plan_source.get("dimensions", []),
            "filters": query_plan_source.get("filters", []),
            "join_path": query_plan_source.get("join_path", []),
            "time_context": query_plan_source.get("time_context", {}),
            "version_context": query_plan_source.get("version_context"),
            "inherit_context": query_plan_source.get("inherit_context", classification.inherit_context),
            "context_delta": query_plan_source.get("context_delta", classification.context_delta.model_dump(mode="json")),
            "need_clarification": query_plan_source.get("need_clarification", classification.need_clarification),
            "clarification_question": query_plan_source.get("clarification_question", classification.clarification_question),
            "reason_code": query_plan_source.get("reason_code", classification.reason_code),
            "analysis_mode": query_plan_source.get("analysis_mode"),
            "sort": query_plan_source.get("sort", []),
            "limit": query_plan_source.get("limit", restored_state.limit or 200),
            "reason": query_plan_source.get("reason", classification.reason),
        })

        retrieval = RetrievalContext(
            domains=[classification.subject_domain] if classification.subject_domain != "unknown" else [],
            metrics=query_plan.metrics,
            retrieval_terms=[],
            retrieval_channels=retrieve_metadata.get("channels", []),
            hits=[],
            hit_count_by_source=retrieve_metadata.get("hit_count_by_source", {}),
            hit_count_by_channel=retrieve_metadata.get("hit_count_by_channel", {}),
        )

        plan_validation = ValidationResponse(
            valid=bool(query_log.plan_valid),
            errors=validate_plan_metadata.get("errors", []),
            warnings=validate_plan_metadata.get("warnings", []),
            risk_level=query_log.plan_risk_level or "low",
            risk_flags=query_log.plan_risk_flags,
        )
        sql_validation = ValidationResponse(
            valid=bool(query_log.sql_valid) if query_log.sql_valid is not None else bool(sql_audit is not None and sql_audit.sql_valid),
            errors=sql_audit.errors if sql_audit is not None else [],
            warnings=sql_audit.warnings if sql_audit is not None else [],
            risk_level=sql_audit.sql_risk_level if sql_audit is not None and sql_audit.sql_risk_level else "low",
            risk_flags=sql_audit.sql_risk_flags if sql_audit is not None else [],
        )

        answer_status = normalize_answer_status(query_log.answer_status, executed=bool(query_log.executed))
        answer = AnswerPayload(
            status=answer_status,
            summary=(
                self._assistant_summary(messages, query_log.trace_id)
                or query_log.answer_status
                or ("历史响应已恢复。" if answer_status == "ok" else "历史响应未记录完整答案状态。")
            ),
        )

        execution = self._restore_execution(
            query_log=query_log,
            sql_audit=sql_audit,
            execute_metadata=execute_metadata,
        )

        return ChatResponse(
            classification=classification,
            query_intent=query_intent,
            retrieval=retrieval,
            trace=trace,
            answer=answer,
            query_plan=query_plan,
            sql=sql_audit.sql_text if sql_audit is not None else None,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
            execution=execution,
            next_session_state=restored_state,
        )

    def _restore_execution(
        self,
        *,
        query_log: RuntimeQueryLogRecord,
        sql_audit: RuntimeSqlAuditRecord | None,
        execute_metadata: dict,
    ) -> ExecutionResponse | None:
        status = execute_metadata.get("status") or self._warning_value(query_log.warnings, "execution_status")
        if query_log.answer_status in {"invalid", "clarification_needed", "chat"} and not query_log.executed:
            return None
        if status is None and sql_audit is None and not query_log.executed:
            return None
        return ExecutionResponse(
            executed=bool(query_log.executed),
            status=status or ("ok" if query_log.executed else "sql_missing"),
            sql=sql_audit.sql_text if sql_audit is not None else None,
            row_count=query_log.row_count or 0,
            columns=[],
            rows=[],
            errors=sql_audit.errors if sql_audit is not None else [],
            warnings=sql_audit.warnings if sql_audit is not None else [],
            elapsed_ms=execute_metadata.get("elapsed_ms"),
            error_category=self._warning_value(query_log.warnings, "execution_error_category"),
            truncated=bool(status == "truncated"),
        )

    def _normalize_legacy_answer_payload(self, payload: dict) -> None:
        answer = payload.get("answer")
        if not isinstance(answer, dict):
            return
        execution = payload.get("execution")
        executed = isinstance(execution, dict) and bool(execution.get("executed"))
        normalized_answer = dict(answer)
        normalized_status = normalize_answer_status(normalized_answer.get("status"), executed=executed)
        if normalized_answer.get("status") == normalized_status:
            return
        normalized_answer["status"] = normalized_status
        normalized_answer["summary"] = normalized_answer.get("summary") or (
            "历史响应已恢复。" if normalized_status == "ok" else "历史响应未记录完整答案状态。"
        )
        payload["answer"] = normalized_answer

    def _assistant_summary(self, messages: list[ChatMessage], trace_id: str) -> str | None:
        for message in reversed(messages):
            if message.role == "assistant" and message.trace_id == trace_id:
                return message.content
        for message in reversed(messages):
            if message.role == "assistant":
                return message.content
        return None

    def _response_snapshot_payload(self, trace: TraceRecord) -> dict | None:
        metadata = self._step_metadata(trace, "response_snapshot")
        payload = metadata.get("response")
        return payload if isinstance(payload, dict) else None

    def _step_metadata(self, trace: TraceRecord, step_name: str) -> dict:
        for step in trace.steps:
            if step.name == step_name and step.metadata:
                return step.metadata
        return {}

    def _warning_value(self, warnings: list[str], prefix: str) -> str | None:
        prefix_with_sep = f"{prefix}:"
        for item in warnings:
            if item.startswith(prefix_with_sep):
                return item[len(prefix_with_sep):]
        return None
