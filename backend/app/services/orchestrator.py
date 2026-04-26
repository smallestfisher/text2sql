from __future__ import annotations

import logging

from backend.app.logging_config import clear_trace_id, set_trace_id
from backend.app.models.api import ChatResponse, PlanRequest, ValidationResponse
from backend.app.models.session_state import SessionState
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.llm_client import LLMClient
from backend.app.services.permission_service import PermissionService
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_plan_compiler import QueryPlanCompiler
from backend.app.services.query_plan_validator import QueryPlanValidator
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.retrieval_service import RetrievalService
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.services.session_service import SessionService
from backend.app.services.session_state_service import SessionStateService
from backend.app.services.sql_executor import SqlExecutor
from backend.app.services.sql_validator import SqlValidator


logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    def __init__(
        self,
        query_planner: QueryPlanner,
        query_plan_validator: QueryPlanValidator,
        permission_service: PermissionService,
        query_plan_compiler: QueryPlanCompiler,
        session_state_service: SessionStateService,
        sql_validator: SqlValidator,
        sql_executor: SqlExecutor,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        answer_builder: AnswerBuilder,
        retrieval_service: RetrievalService,
        session_service: SessionService,
        audit_service: AuditService,
        runtime_log_repository: DbRuntimeLogRepository,
        semantic_layer: dict,
    ) -> None:
        self.query_planner = query_planner
        self.query_plan_validator = query_plan_validator
        self.permission_service = permission_service
        self.query_plan_compiler = query_plan_compiler
        self.session_state_service = session_state_service
        self.sql_validator = sql_validator
        self.sql_executor = sql_executor
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client
        self.answer_builder = answer_builder
        self.retrieval_service = retrieval_service
        self.session_service = session_service
        self.audit_service = audit_service
        self.runtime_log_repository = runtime_log_repository
        self.semantic_layer = semantic_layer

    def chat(self, request: PlanRequest) -> ChatResponse:
        trace = self.audit_service.new_trace()
        set_trace_id(trace.trace_id)
        try:
            warnings: list[str] = []
            logger.info(
                "chat start trace_id=%s session_id=%s question=%s",
                trace.trace_id,
                request.session_id,
                request.question,
            )

            session_state = request.session_state
            if request.session_id and session_state is None:
                session_state = self.session_service.resolve_state(request.session_id)
            self.audit_service.append_step(trace, "load_session", "completed", "session state resolved")

            semantic_parse, classification, query_plan, planning_warnings = self.query_planner.create_plan(
                question=request.question,
                session_state=session_state,
            )
            warnings.extend(planning_warnings)
            logger.info(
                "classification trace_id=%s type=%s domain=%s inherit=%s need_clarification=%s",
                trace.trace_id,
                classification.question_type,
                classification.subject_domain,
                classification.inherit_context,
                classification.need_clarification,
            )
            self.audit_service.append_step(
                trace,
                "plan",
                "completed",
                classification.question_type,
                metadata={
                    "classification": classification.model_dump(),
                    "semantic_parse": semantic_parse.model_dump(),
                    "session_semantic_diff": self.query_planner.semantic_runtime.session_semantic_diff(
                        semantic_parse,
                        session_state,
                    ),
                    "query_plan_summary": {
                        "subject_domain": query_plan.subject_domain,
                        "semantic_views": query_plan.semantic_views,
                        "metrics": query_plan.metrics,
                        "dimensions": query_plan.dimensions,
                        "filter_fields": [item.field for item in query_plan.filters],
                    },
                },
            )
            self._sync_classification_with_query_plan(classification, query_plan)
            terminal_reason = self._terminal_skip_reason(classification, query_plan)
            if terminal_reason is not None:
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
                return self._finalize_terminal_response(
                    trace=trace,
                    request=request,
                    session_state=session_state,
                    semantic_parse=semantic_parse,
                    classification=classification,
                    query_plan=query_plan,
                    warnings=warnings,
                    retrieval=None,
                    terminal_reason=terminal_reason,
                )

            retrieval = self.retrieval_service.retrieve(semantic_parse)
            retrieval_summary = self.retrieval_service.summarize_retrieval(retrieval)
            logger.info(
                "retrieval trace_id=%s hits=%s views=%s metrics=%s",
                trace.trace_id,
                len(retrieval.hits),
                retrieval.semantic_views,
                retrieval.metrics,
            )
            self.audit_service.append_step(
                trace,
                "retrieve",
                "completed",
                f"{len(retrieval.hits)} hits",
                metadata=retrieval_summary,
            )

            query_plan_prompt = self.prompt_builder.build_query_plan_prompt(
                question=request.question,
                semantic_parse=semantic_parse,
                retrieval=retrieval,
                base_plan=query_plan,
                session_state=session_state,
            )
            base_query_plan = query_plan.model_copy(deep=True)
            llm_plan_hint = self.llm_client.generate_query_plan_hint(query_plan_prompt)
            plan_hint_detail = "stub prompt built"
            plan_hint_metadata = {
                "mode": llm_plan_hint.get("mode"),
                "model": llm_plan_hint.get("model"),
                "attempt": llm_plan_hint.get("attempt"),
            }
            if llm_plan_hint.get("mode") == "live":
                candidate_query_plan = self.query_plan_compiler.apply_llm_hint(query_plan, llm_plan_hint)
                acceptable, rejection_reasons = self.query_plan_compiler.semantic_runtime.llm_plan_is_acceptable(
                    candidate_query_plan,
                    base_query_plan,
                )
                if acceptable:
                    query_plan = candidate_query_plan
                    plan_hint_detail = "live llm query plan hint accepted"
                else:
                    plan_hint_detail = "live llm query plan hint rejected"
                    plan_hint_metadata["rejection_reasons"] = rejection_reasons
                    warnings.append(
                        "llm query plan hint rejected before compile: " + "; ".join(rejection_reasons)
                    )
            self.audit_service.append_step(
                trace,
                "build_query_plan_prompt",
                "completed",
                plan_hint_detail,
                metadata=plan_hint_metadata,
            )

            query_plan, permission_warnings = self.permission_service.apply_to_query_plan(
                query_plan=query_plan,
                user_context=request.user_context,
            )
            warnings.extend(permission_warnings)
            self.audit_service.append_step(trace, "authorize", "completed", "permission filters applied")

            query_plan = self.query_plan_compiler.compile(query_plan=query_plan, retrieval=retrieval)
            logger.info(
                "plan trace_id=%s views=%s tables=%s metrics=%s dimensions=%s",
                trace.trace_id,
                query_plan.semantic_views,
                query_plan.tables,
                query_plan.metrics,
                query_plan.dimensions,
            )
            self.audit_service.append_step(
                trace,
                "compile_plan",
                "completed",
                "query plan compiled",
                metadata={
                    "compiled_plan": query_plan.model_dump(mode="json")
                },
            )

            plan_result = self.query_plan_validator.validate_detailed(
                query_plan=query_plan,
                semantic_layer=self.semantic_layer,
            )
            plan_errors = plan_result.errors
            plan_warnings = plan_result.warnings
            if plan_errors and llm_plan_hint.get("mode") == "live":
                fallback_plan, fallback_permission_warnings = self.permission_service.apply_to_query_plan(
                    query_plan=base_query_plan.model_copy(deep=True),
                    user_context=request.user_context,
                )
                fallback_plan = self.query_plan_compiler.compile(query_plan=fallback_plan, retrieval=retrieval)
                fallback_result = self.query_plan_validator.validate_detailed(
                    query_plan=fallback_plan,
                    semantic_layer=self.semantic_layer,
                )
                fallback_errors = fallback_result.errors
                fallback_warnings = fallback_result.warnings
                if not fallback_errors:
                    warnings.append("llm query plan hint rejected; fallback to local planner result")
                    query_plan = fallback_plan
                    permission_warnings = fallback_permission_warnings
                    plan_errors = []
                    plan_warnings = fallback_warnings
                    plan_result = fallback_result
            warnings.extend(plan_warnings)
            logger.info(
                "plan validation trace_id=%s valid=%s errors=%s warnings=%s",
                trace.trace_id,
                not plan_errors,
                len(plan_errors),
                len(plan_warnings),
            )
            self.audit_service.append_step(
                trace,
                "validate_plan",
                "completed" if not plan_errors else "failed",
                metadata={
                    "error_count": len(plan_errors),
                    "warning_count": len(plan_warnings),
                    "errors": plan_errors,
                    "warnings": plan_warnings,
                },
            )
            self._sync_classification_with_query_plan(classification, query_plan)
            terminal_reason = self._terminal_skip_reason(classification, query_plan)
            if terminal_reason is not None:
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
                return self._finalize_terminal_response(
                    trace=trace,
                    request=request,
                    session_state=session_state,
                    semantic_parse=semantic_parse,
                    classification=classification,
                    query_plan=query_plan,
                    warnings=warnings,
                    retrieval=retrieval,
                    terminal_reason=terminal_reason,
                    plan_validation=ValidationResponse(
                        valid=not plan_errors,
                        errors=plan_errors,
                        warnings=warnings,
                        risk_level=plan_result.risk_level,
                        risk_flags=plan_result.risk_flags,
                    ),
                )

            llm_sql = None
            sql_hint_metadata = {"mode": "stub", "used": False}
            sql_prompt = None
            if not plan_errors:
                sql_prompt = self.prompt_builder.build_sql_prompt(query_plan)
                prompt_context_metadata = {
                    "context_budget": sql_prompt.get("context_budget"),
                    "context_summary": sql_prompt.get("context_summary"),
                }
                llm_sql = self.llm_client.generate_sql_hint(sql_prompt)
                if llm_sql:
                    sql_hint_metadata = {"mode": "live", "used": True}
                    self.audit_service.append_step(
                        trace,
                        "build_sql_prompt",
                        "completed",
                        "live sql hint returned",
                        metadata={**sql_hint_metadata, **prompt_context_metadata},
                    )
                else:
                    self.audit_service.append_step(
                        trace,
                        "build_sql_prompt",
                        "completed",
                        "llm sql unavailable",
                        metadata={**sql_hint_metadata, **prompt_context_metadata},
                    )

            sql = None
            if not plan_errors:
                sql = llm_sql
            logger.info(
                "sql generation trace_id=%s generated=%s llm_used=%s",
                trace.trace_id,
                bool(sql),
                bool(llm_sql),
            )
            visible_sql = sql if self.permission_service.can_view_sql(request.user_context) else None
            self.audit_service.append_step(
                trace,
                "generate_sql",
                "completed" if sql else "skipped",
                metadata={
                    "used_sources": query_plan.semantic_views or query_plan.tables,
                    "sql_visible": bool(visible_sql),
                },
            )

            required_filter_fields = self.permission_service.required_filter_fields(
                query_plan=query_plan,
                user_context=request.user_context,
            )
            sql_result = (
                self.sql_validator.validate_detailed(
                    sql,
                    self.semantic_layer,
                    query_plan=query_plan,
                    required_filter_fields=required_filter_fields,
                )
                if sql is not None
                else None
            )
            sql_errors = ["sql is empty"] if sql is None and not plan_errors else (sql_result.errors if sql_result else [])
            sql_warnings = sql_result.warnings if sql_result is not None else []
            sql_risk_level = sql_result.risk_level if sql_result is not None else "low"
            sql_risk_flags = sql_result.risk_flags if sql_result is not None else []
            if sql_errors and llm_sql and not plan_errors and sql_prompt is not None:
                repaired_sql = self.llm_client.repair_sql(
                    prompt_payload=sql_prompt,
                    sql=sql,
                    errors=sql_errors,
                    warnings=sql_warnings,
                )
                if repaired_sql:
                    repaired_sql_result = self.sql_validator.validate_detailed(
                        repaired_sql,
                        self.semantic_layer,
                        query_plan=query_plan,
                        required_filter_fields=required_filter_fields,
                    )
                    if not repaired_sql_result.errors:
                        warnings.append("llm sql repaired after validation failure")
                        sql_hint_metadata["repair_used"] = True
                        sql = repaired_sql
                        visible_sql = sql if self.permission_service.can_view_sql(request.user_context) else None
                        sql_errors = []
                        sql_warnings = repaired_sql_result.warnings
                        sql_risk_level = repaired_sql_result.risk_level
                        sql_risk_flags = repaired_sql_result.risk_flags

            self.audit_service.append_step(
                trace,
                "validate_sql",
                "completed" if not sql_errors else "failed",
                metadata={
                    "llm_sql_used": bool(sql_hint_metadata.get("used")),
                    "llm_sql_mode": sql_hint_metadata.get("mode"),
                    "fallback_reason": sql_hint_metadata.get("fallback_reason"),
                    "error_count": len(sql_errors),
                    "warning_count": len(sql_warnings),
                    "errors": sql_errors,
                    "warnings": sql_warnings,
                    "risk_level": sql_risk_level,
                    "risk_flags": sql_risk_flags,
                    "repair_used": bool(sql_hint_metadata.get("repair_used")),
                },
            )
            logger.info(
                "sql validation trace_id=%s valid=%s errors=%s warnings=%s",
                trace.trace_id,
                not sql_errors,
                len(sql_errors),
                len(sql_warnings),
            )

            execution = None if (plan_errors or sql_errors) else self.sql_executor.execute(
                sql=sql,
                user_context=request.user_context,
            )
            if (
                execution is not None
                and not execution.executed
                and llm_sql
                and sql_prompt is not None
                and self.llm_client.enabled
            ):
                repaired_sql = self.llm_client.repair_sql(
                    prompt_payload=sql_prompt,
                    sql=sql,
                    errors=execution.errors,
                    warnings=execution.warnings,
                )
                if repaired_sql:
                    repaired_sql_result = self.sql_validator.validate_detailed(
                        repaired_sql,
                        self.semantic_layer,
                        query_plan=query_plan,
                        required_filter_fields=required_filter_fields,
                    )
                    if not repaired_sql_result.errors:
                        repaired_execution = self.sql_executor.execute(
                            sql=repaired_sql,
                            user_context=request.user_context,
                        )
                        if repaired_execution.executed:
                            warnings.append("llm sql repaired after execution failure")
                            sql_hint_metadata["repair_used"] = True
                            sql = repaired_sql
                            visible_sql = sql if self.permission_service.can_view_sql(request.user_context) else None
                            sql_errors = []
                            sql_warnings = repaired_sql_result.warnings
                            sql_risk_level = repaired_sql_result.risk_level
                            sql_risk_flags = repaired_sql_result.risk_flags
                            execution = repaired_execution
            execution = self.permission_service.apply_to_execution(
                execution=execution,
                user_context=request.user_context,
            )
            if execution is not None and not self.permission_service.can_view_sql(request.user_context):
                execution.sql = None
            self.audit_service.append_step(
                trace,
                "execute",
                "completed" if execution else "skipped",
                metadata={
                    "status": execution.status if execution else None,
                    "row_count": execution.row_count if execution else None,
                    "elapsed_ms": execution.elapsed_ms if execution else None,
                    "warning_count": len(execution.warnings) if execution else 0,
                    "error_count": len(execution.errors) if execution else 0,
                },
            )
            logger.info(
                "execution trace_id=%s executed=%s status=%s row_count=%s elapsed_ms=%s",
                trace.trace_id,
                bool(execution),
                execution.status if execution else None,
                execution.row_count if execution else None,
                execution.elapsed_ms if execution else None,
            )

            plan_validation = ValidationResponse(
                valid=not plan_errors,
                errors=plan_errors,
                warnings=warnings,
                risk_level=plan_result.risk_level,
                risk_flags=plan_result.risk_flags,
            )
            sql_validation = ValidationResponse(
                valid=not sql_errors,
                errors=sql_errors,
                warnings=sql_warnings,
                risk_level=sql_risk_level,
                risk_flags=sql_risk_flags,
            )
            answer = self.answer_builder.build(
                classification=classification,
                query_plan=query_plan,
                execution=execution,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
            )

            next_session_state = self.session_state_service.build_next_state(
                query_plan=query_plan,
                previous_state=session_state,
                sql=sql,
            )
            if not self.permission_service.can_view_sql(request.user_context):
                next_session_state.last_sql = None

            if request.session_id:
                self.session_service.append_user_message(request.session_id, request.question, trace.trace_id)
                assistant_text = answer.summary
                self.session_service.append_assistant_message(request.session_id, assistant_text, trace.trace_id)
                next_session_state.session_id = request.session_id
                self.session_service.update_state(request.session_id, next_session_state, trace_id=trace.trace_id)
            response = ChatResponse(
                classification=classification,
                semantic_parse=semantic_parse,
                retrieval=retrieval,
                trace=trace,
                answer=answer,
                query_plan=query_plan,
                sql=visible_sql,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
                next_session_state=next_session_state,
            )
            self._append_response_snapshot(trace, response)
            self._persist_runtime_artifacts(
                trace=trace,
                request=request,
                retrieval=retrieval,
                classification=classification,
                answer=answer,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
                sql=sql,
                warnings=warnings + sql_warnings,
            )
            logger.info(
                "chat completed trace_id=%s answer_status=%s plan_valid=%s sql_valid=%s",
                trace.trace_id,
                answer.status if answer else None,
                plan_validation.valid,
                sql_validation.valid,
            )
            return response
        finally:
            clear_trace_id()

    def _sync_classification_with_query_plan(self, classification, query_plan) -> None:
        if classification.question_type == "invalid":
            return
        if not query_plan.need_clarification:
            return
        query_plan.question_type = "clarification_needed"
        classification.question_type = "clarification_needed"
        classification.need_clarification = True
        classification.inherit_context = False
        classification.reason = query_plan.reason or classification.reason
        classification.reason_code = query_plan.reason_code or classification.reason_code
        classification.clarification_question = (
            query_plan.clarification_question
            or classification.clarification_question
            or query_plan.reason
            or "请补充查询目标、时间范围或统计口径。"
        )

    def _terminal_skip_reason(self, classification, query_plan) -> str | None:
        if classification.question_type == "invalid":
            return "terminal gate: invalid question, skip retrieval and SQL generation"
        if classification.need_clarification or query_plan.need_clarification:
            return "terminal gate: clarification required, skip SQL generation"
        return None

    def _finalize_terminal_response(
        self,
        *,
        trace,
        request: PlanRequest,
        session_state: SessionState | None,
        semantic_parse,
        classification,
        query_plan,
        warnings: list[str],
        retrieval,
        terminal_reason: str,
        plan_validation: ValidationResponse | None = None,
    ) -> ChatResponse:
        sql_validation = ValidationResponse(
            valid=True,
            errors=[],
            warnings=[terminal_reason],
            risk_level="low",
            risk_flags=[],
        )
        if plan_validation is None:
            plan_validation = ValidationResponse(
                valid=True,
                errors=[],
                warnings=warnings + [terminal_reason],
                risk_level="low",
                risk_flags=[],
            )
        answer = self.answer_builder.build(
            classification=classification,
            query_plan=query_plan,
            execution=None,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
        )
        next_session_state = self._preserved_session_state(session_state, request.session_id)

        if request.session_id:
            self.session_service.append_user_message(request.session_id, request.question, trace.trace_id)
            self.session_service.append_assistant_message(request.session_id, answer.summary, trace.trace_id)
            next_session_state.session_id = request.session_id
            self.session_service.update_state(request.session_id, next_session_state, trace_id=trace.trace_id)

        response = ChatResponse(
            classification=classification,
            semantic_parse=semantic_parse,
            retrieval=retrieval,
            trace=trace,
            answer=answer,
            query_plan=query_plan,
            sql=None,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
            execution=None,
            next_session_state=next_session_state,
        )
        self._append_response_snapshot(trace, response)
        self._persist_runtime_artifacts(
            trace=trace,
            request=request,
            retrieval=retrieval,
            classification=classification,
            answer=answer,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
            execution=None,
            sql=None,
            warnings=warnings + sql_validation.warnings,
        )
        logger.info(
            "chat completed trace_id=%s answer_status=%s terminal=%s",
            trace.trace_id,
            answer.status if answer else None,
            classification.question_type,
        )
        return response

    def _preserved_session_state(
        self,
        session_state: SessionState | None,
        session_id: str | None,
    ) -> SessionState:
        if session_state is not None:
            preserved = session_state.model_copy(deep=True)
            if session_id:
                preserved.session_id = session_id
            return preserved
        return SessionState(session_id=session_id or "session_pending")

    def _persist_runtime_artifacts(
        self,
        *,
        trace,
        request: PlanRequest,
        retrieval,
        classification,
        answer,
        plan_validation: ValidationResponse,
        sql_validation: ValidationResponse,
        execution,
        sql: str | None,
        warnings: list[str],
    ) -> None:
        try:
            self.audit_service.finalize(trace, warnings=warnings)
        except Exception:
            logger.exception("failed to persist audit trace trace_id=%s", trace.trace_id)

        if retrieval is not None:
            try:
                self.runtime_log_repository.log_retrieval(trace.trace_id, retrieval)
            except Exception:
                logger.exception("failed to persist retrieval log trace_id=%s", trace.trace_id)

        try:
            self.runtime_log_repository.log_sql_audit(
                trace_id=trace.trace_id,
                sql=sql,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
            )
        except Exception:
            logger.exception("failed to persist sql audit trace_id=%s", trace.trace_id)

        try:
            self.runtime_log_repository.log_query(
                trace_id=trace.trace_id,
                session_id=request.session_id,
                user_id=request.user_context.user_id if request.user_context else None,
                question=request.question,
                question_type=classification.question_type,
                subject_domain=classification.subject_domain,
                answer_status=answer.status if answer else None,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
                warnings=warnings,
            )
        except Exception:
            logger.exception("failed to persist query log trace_id=%s", trace.trace_id)

    def _append_response_snapshot(self, trace, response: ChatResponse) -> None:
        payload = response.model_dump(mode="json", exclude={"trace", "sql"})
        execution_payload = payload.get("execution")
        if isinstance(execution_payload, dict):
            execution_payload["sql"] = None
        state_payload = payload.get("next_session_state")
        if isinstance(state_payload, dict):
            state_payload["last_sql"] = None
        self.audit_service.append_step(
            trace,
            "response_snapshot",
            "completed",
            "response snapshot persisted",
            metadata={
                "schema_version": 1,
                "response": payload,
            },
        )
