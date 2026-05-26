from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging

from backend.app.core.cancellation import CancellationToken
from backend.app.core.exceptions import ClientCancelledError
from backend.app.logging_config import clear_trace_id, set_trace_id
from backend.app.models.api import ChatResponse, PlanRequest, ValidationResponse
from backend.app.models.progress import ProgressEvent
from backend.app.models.session_state import SessionState
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.conversation_persistence_service import ConversationPersistenceService
from backend.app.services.llm_client import LLMClient
from backend.app.services.progress_service import ProgressService
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_plan_compiler import QueryPlanCompiler
from backend.app.services.query_plan_validator import QueryPlanValidator
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.session_service import SessionService
from backend.app.services.session_state_service import SessionStateService
from backend.app.services.sql_executor import SqlExecutor
from backend.app.services.sql_validator import SqlValidator


logger = logging.getLogger(__name__)
_OMITTED = object()


class ConversationOrchestrator:
    def __init__(
        self,
        query_planner: QueryPlanner,
        query_plan_validator: QueryPlanValidator,
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
        progress_service: ProgressService,
        runtime_log_repository: DbRuntimeLogRepository,
        conversation_persistence_service: ConversationPersistenceService,
        domain_config: dict,
    ) -> None:
        self.query_planner = query_planner
        self.query_plan_validator = query_plan_validator
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
        self.progress_service = progress_service
        self.runtime_log_repository = runtime_log_repository
        self.conversation_persistence_service = conversation_persistence_service
        self.domain_config = domain_config

    def chat(
        self,
        request: PlanRequest,
        trace_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> ChatResponse:
        trace = self.audit_service.new_trace(trace_id=trace_id)
        set_trace_id(trace.trace_id)

        warnings: list[str] = []
        session_state = request.session_state
        query_intent = None
        classification = None
        query_plan = None
        retrieval = None
        sql = None
        execution = None
        plan_validation: ValidationResponse | None = None
        sql_validation: ValidationResponse | None = None

        try:
            logger.info(
                "chat start trace_id=%s session_id=%s question=%s",
                trace.trace_id,
                request.session_id,
                request.question,
            )
            self._log_stage_io(
                "request",
                inputs={
                    "question": request.question,
                    "session_id": request.session_id,
                    "has_session_state": request.session_state is not None,
                    "user_id": request.user_context.user_id if request.user_context else None,
                },
            )
            self._raise_if_cancelled(cancellation_token, stage="request accepted")
            self._publish_progress(
                trace.trace_id,
                event_type="accepted",
                stage="accepted",
                status="queued",
                detail="request accepted",
            )

            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="load_session",
                status="running",
                detail="loading session state",
            )
            if request.session_id and session_state is None:
                session_state = self.session_service.resolve_state(request.session_id)
            self._raise_if_cancelled(cancellation_token, stage="load session")
            self._log_stage_io(
                "load_session",
                inputs={"session_id": request.session_id, "provided_state": request.session_state is not None},
                outputs={"session_state": self._session_state_summary(session_state)},
            )
            self.audit_service.append_step(trace, "load_session", "completed", "session state resolved")
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="load_session",
                status="completed",
                detail="session state resolved",
            )

            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="planning",
                status="running",
                detail="building query plan",
            )
            planning_trace = self.query_planner.build_planning_trace(
                question=request.question,
                session_state=session_state,
                cancellation_token=cancellation_token,
            )
            self._raise_if_cancelled(cancellation_token, stage="planning")
            query_intent = planning_trace["query_intent"]
            classification = planning_trace["classification"]
            planning_warnings = planning_trace["warnings"]
            parser_intent = planning_trace["parser_intent"]
            llm_intent = planning_trace["llm_intent"]
            normalized_intent = planning_trace["normalized_intent"]
            intent_selection = planning_trace["intent_selection"]
            llm_diff = planning_trace["llm_diff"]
            normalized_diff = planning_trace["normalized_diff"]
            semantic_diff = planning_trace["semantic_diff"]
            warnings.extend(planning_warnings)

            self._log_stage_io(
                "planning",
                inputs={"question": request.question, "session_state": self._session_state_summary(session_state)},
                outputs={
                    "intent": self._intent_summary(query_intent),
                    "classification": self._classification_summary(classification),
                    "warnings": planning_warnings,
                    "intent_selection": intent_selection,
                },
            )

            logger.info(
                "parser trace trace_id=%s domain=%s metrics=%s entities=%s dimensions=%s filters=%s time_grain=%s version=%s follow_up_cue=%s explicit_slots=%s",
                trace.trace_id,
                query_intent.subject_domain,
                query_intent.matched_metrics,
                query_intent.matched_entities,
                query_intent.requested_dimensions,
                [item.field for item in query_intent.filters],
                query_intent.time_context.grain,
                bool(query_intent.version_context),
                query_intent.has_follow_up_cue,
                query_intent.has_explicit_slots,
            )
            self.audit_service.append_step(
                trace,
                "parse_intent",
                "completed",
                "parser intent built",
                metadata={
                    "parser_intent": parser_intent.model_dump(mode="json"),
                    "parser_signals": planning_trace["parser_signals"],
                },
            )
            self.audit_service.append_step(
                trace,
                "llm_intent",
                llm_intent["status"],
                llm_intent.get("reason") or llm_intent["status"],
                metadata={
                    "intent": llm_intent["intent"].model_dump(mode="json") if llm_intent.get("intent") is not None else None,
                    "raw": llm_intent.get("raw"),
                    "diff_vs_parser": llm_diff,
                },
            )
            self.audit_service.append_step(
                trace,
                "normalized_intent",
                normalized_intent["status"],
                ", ".join(normalized_intent.get("warnings", [])) or normalized_intent["status"],
                metadata={
                    "intent": normalized_intent["intent"].model_dump(mode="json") if normalized_intent.get("intent") is not None else None,
                    "warnings": normalized_intent.get("warnings", []),
                    "diff_vs_llm_intent": normalized_diff,
                    "intent_selection": intent_selection,
                },
            )

            query_plan = self.query_planner.build_plan_from_intent(
                query_intent=query_intent,
                classification=classification,
                session_state=session_state,
            )
            self._log_stage_io(
                "plan_from_intent",
                inputs={
                    "intent": self._intent_summary(query_intent),
                    "classification": self._classification_summary(classification),
                },
                outputs={"query_plan": self._query_plan_summary(query_plan)},
            )
            logger.info(
                "classification trace_id=%s type=%s domain=%s inherit=%s need_clarification=%s semantic_diff=%s",
                trace.trace_id,
                classification.question_type,
                classification.subject_domain,
                classification.inherit_context,
                classification.need_clarification,
                semantic_diff,
            )
            self.audit_service.append_step(
                trace,
                "classify_question",
                "completed",
                classification.question_type,
                metadata={
                    "classification": classification.model_dump(mode="json"),
                    "classifier_debug": planning_trace.get("classifier_debug", {}),
                    "session_semantic_diff": semantic_diff,
                },
            )
            self.audit_service.append_step(
                trace,
                "plan",
                "completed",
                classification.question_type,
                metadata={
                    "classification": classification.model_dump(),
                    "query_intent": query_intent.model_dump(),
                    "session_semantic_diff": semantic_diff,
                    "query_plan_summary": {
                        "subject_domain": query_plan.subject_domain,
                        "tables": query_plan.tables,
                        "metrics": query_plan.metrics,
                        "dimensions": query_plan.dimensions,
                        "filter_fields": [item.field for item in query_plan.filters],
                    },
                },
            )
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="planning",
                status="completed",
                detail=classification.question_type,
            )
            self._sync_classification_with_query_plan(classification, query_plan)
            terminal_reason = self._terminal_skip_reason(classification, query_plan)
            if terminal_reason is not None:
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
                self._raise_if_cancelled(cancellation_token, stage="terminal response")
                return self._finalize_terminal_response(
                    trace=trace,
                    request=request,
                    session_state=session_state,
                    query_intent=query_intent,
                    classification=classification,
                    query_plan=query_plan,
                    warnings=warnings,
                    retrieval=None,
                    terminal_reason=terminal_reason,
                )

            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="retrieval",
                status="running",
                detail="retrieving examples and knowledge",
            )
            retrieval = self.retrieval_service.retrieve(query_intent)
            self._raise_if_cancelled(cancellation_token, stage="retrieval")
            retrieval_summary = self.retrieval_service.summarize_retrieval(retrieval)
            self._log_stage_io(
                "retrieval",
                inputs={"intent": self._intent_summary(query_intent)},
                outputs={"retrieval": self._retrieval_summary(retrieval)},
            )
            logger.info(
                "retrieval trace_id=%s hits=%s metrics=%s",
                trace.trace_id,
                len(retrieval.hits),
                retrieval.metrics,
            )
            self.audit_service.append_step(
                trace,
                "retrieve",
                "completed",
                f"{len(retrieval.hits)} hits",
                metadata=retrieval_summary,
            )
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="retrieval",
                status="completed",
                detail=f"{len(retrieval.hits)} hits",
            )

            query_plan = self.query_plan_compiler.compile(query_plan=query_plan, retrieval=retrieval)
            self._raise_if_cancelled(cancellation_token, stage="compile plan")
            self._log_stage_io(
                "compile_plan",
                inputs={"retrieval": self._retrieval_summary(retrieval)},
                outputs={"query_plan": self._query_plan_summary(query_plan)},
            )
            logger.info(
                "plan trace_id=%s tables=%s metrics=%s dimensions=%s",
                trace.trace_id,
                query_plan.tables,
                query_plan.metrics,
                query_plan.dimensions,
            )
            self.audit_service.append_step(
                trace,
                "compile_plan",
                "completed",
                "query plan compiled",
                metadata={"compiled_plan": query_plan.model_dump(mode="json")},
            )

            plan_result = self.query_plan_validator.validate_detailed(
                query_plan=query_plan,
                domain_config=self.domain_config,
            )
            plan_errors = plan_result.errors
            plan_warnings = plan_result.warnings
            warnings.extend(plan_warnings)
            self._log_stage_io(
                "validate_plan",
                inputs={"query_plan": self._query_plan_summary(query_plan)},
                outputs={
                    "valid": not plan_errors,
                    "errors": plan_errors,
                    "warnings": plan_warnings,
                    "risk_level": plan_result.risk_level,
                    "risk_flags": plan_result.risk_flags,
                },
            )
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
            plan_validation = ValidationResponse(
                valid=not plan_errors,
                errors=plan_errors,
                warnings=warnings,
                risk_level=plan_result.risk_level,
                risk_flags=plan_result.risk_flags,
            )
            self._sync_classification_with_query_plan(classification, query_plan)
            terminal_reason = self._terminal_skip_reason(classification, query_plan)
            if terminal_reason is not None:
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
                self._raise_if_cancelled(cancellation_token, stage="terminal response")
                return self._finalize_terminal_response(
                    trace=trace,
                    request=request,
                    session_state=session_state,
                    query_intent=query_intent,
                    classification=classification,
                    query_plan=query_plan,
                    warnings=warnings,
                    retrieval=retrieval,
                    terminal_reason=terminal_reason,
                    plan_validation=plan_validation,
                )

            llm_sql = None
            sql_hint_metadata = {"mode": "not_started", "used": False}
            sql_prompt = None
            if not plan_errors:
                self._publish_progress(
                    trace.trace_id,
                    event_type="stage",
                    stage="sql_generation",
                    status="running",
                    detail="generating sql",
                )
                sql_prompt = self.prompt_builder.build_sql_prompt(
                    query_plan,
                    retrieval=retrieval,
                    question=request.question,
                )
                self._log_stage_io(
                    "build_sql_prompt",
                    inputs={
                        "question": request.question,
                        "query_plan": self._query_plan_summary(query_plan),
                        "retrieval": self._retrieval_summary(retrieval),
                    },
                    outputs={
                        "prompt_keys": sorted(sql_prompt.keys()),
                        "context_budget": sql_prompt.get("context_budget"),
                        "context_summary": sql_prompt.get("context_summary"),
                    },
                )
                prompt_context_metadata = {
                    "context_budget": sql_prompt.get("context_budget"),
                    "context_summary": sql_prompt.get("context_summary"),
                }
                llm_sql = self.llm_client.generate_sql_hint(
                    sql_prompt,
                    cancellation_token=cancellation_token,
                )
                self._raise_if_cancelled(cancellation_token, stage="sql generation")
                if llm_sql:
                    sql_hint_metadata = {"mode": "live", "used": True}
                    self.audit_service.append_step(
                        trace,
                        "build_sql_prompt",
                        "completed",
                        "live sql hint returned",
                        metadata={**sql_hint_metadata, **prompt_context_metadata},
                    )

            if not plan_errors:
                sql = llm_sql
            self._log_stage_io(
                "generate_sql",
                inputs={"plan_valid": not plan_errors, "llm_enabled": self.llm_client.enabled},
                outputs={"sql_present": bool(sql), "sql_preview": self._preview_text(sql)},
            )
            logger.info(
                "sql generation trace_id=%s generated=%s llm_used=%s",
                trace.trace_id,
                bool(sql),
                bool(llm_sql),
            )
            self.audit_service.append_step(
                trace,
                "generate_sql",
                "completed" if sql else "skipped",
                metadata={
                    "used_sources": query_plan.tables,
                    "sql_visible": bool(sql),
                },
            )
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="sql_generation",
                status=("completed" if sql else "skipped"),
                detail=("sql generated" if sql else "sql unavailable"),
            )

            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="sql_validation",
                status="running",
                detail="validating sql",
            )
            required_filter_fields: list[str] = []
            logger.info(
                "sql validation input trace_id=%s sql_present=%s sql_preview=%s query_plan_tables=%s query_plan_dimensions=%s query_plan_metrics=%s",
                trace.trace_id,
                bool(sql),
                (sql[:800] if sql else None),
                query_plan.tables,
                query_plan.dimensions,
                query_plan.metrics,
            )
            sql_result = (
                self.sql_validator.validate_detailed(
                    sql,
                    self.domain_config,
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
            self._log_stage_io(
                "validate_sql",
                inputs={"sql_preview": self._preview_text(sql), "query_plan": self._query_plan_summary(query_plan)},
                outputs={
                    "valid": not sql_errors,
                    "errors": sql_errors,
                    "warnings": sql_warnings,
                    "risk_level": sql_risk_level,
                    "risk_flags": sql_risk_flags,
                },
            )
            if sql_errors and llm_sql and not plan_errors and sql_prompt is not None:
                repaired_sql = self.llm_client.repair_sql(
                    prompt_payload=sql_prompt,
                    sql=sql,
                    errors=sql_errors,
                    warnings=sql_warnings,
                    cancellation_token=cancellation_token,
                )
                if repaired_sql and sql_errors:
                    logger.info(
                        "sql repair candidate trace_id=%s sql_preview=%s errors=%s",
                        trace.trace_id,
                        repaired_sql[:800],
                        sql_errors,
                    )
                    repaired_sql_result = self.sql_validator.validate_detailed(
                        repaired_sql,
                        self.domain_config,
                        query_plan=query_plan,
                        required_filter_fields=required_filter_fields,
                    )
                    if not repaired_sql_result.errors:
                        warnings.append("llm sql repaired after validation failure")
                        sql_hint_metadata["repair_used"] = True
                        sql = repaired_sql
                        sql_errors = []
                        sql_warnings = repaired_sql_result.warnings
                        sql_risk_level = repaired_sql_result.risk_level
                        sql_risk_flags = repaired_sql_result.risk_flags
            self._raise_if_cancelled(cancellation_token, stage="sql validation")

            sql_validation = ValidationResponse(
                valid=not sql_errors,
                errors=sql_errors,
                warnings=sql_warnings,
                risk_level=sql_risk_level,
                risk_flags=sql_risk_flags,
            )
            self.audit_service.append_step(
                trace,
                "validate_sql",
                "completed" if not sql_errors else "failed",
                metadata={
                    "llm_sql_used": bool(sql_hint_metadata.get("used")),
                    "llm_sql_mode": sql_hint_metadata.get("mode"),
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
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="sql_validation",
                status=("completed" if not sql_errors else "failed"),
                detail=("sql valid" if not sql_errors else "sql validation failed"),
                metadata={"errors": sql_errors, "warnings": sql_warnings},
            )

            if not (plan_errors or sql_errors):
                self._publish_progress(
                    trace.trace_id,
                    event_type="stage",
                    stage="execution",
                    status="running",
                    detail="executing sql",
                )
            execution = None if (plan_errors or sql_errors) else self.sql_executor.execute(
                sql=sql,
                user_context=request.user_context,
                cancellation_token=cancellation_token,
            )
            self._log_stage_io(
                "execute_sql",
                inputs={"sql_preview": self._preview_text(sql), "blocked_by_errors": bool(plan_errors or sql_errors)},
                outputs={"execution": self._execution_summary(execution)},
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
                    cancellation_token=cancellation_token,
                )
                if repaired_sql:
                    repaired_sql_result = self.sql_validator.validate_detailed(
                        repaired_sql,
                        self.domain_config,
                        query_plan=query_plan,
                        required_filter_fields=required_filter_fields,
                    )
                    if not repaired_sql_result.errors:
                        repaired_execution = self.sql_executor.execute(
                            sql=repaired_sql,
                            user_context=request.user_context,
                            cancellation_token=cancellation_token,
                        )
                        if repaired_execution.executed:
                            warnings.append("llm sql repaired after execution failure")
                            sql_hint_metadata["repair_used"] = True
                            sql = repaired_sql
                            sql_errors = []
                            sql_warnings = repaired_sql_result.warnings
                            sql_risk_level = repaired_sql_result.risk_level
                            sql_risk_flags = repaired_sql_result.risk_flags
                            sql_validation = ValidationResponse(
                                valid=True,
                                errors=[],
                                warnings=sql_warnings,
                                risk_level=sql_risk_level,
                                risk_flags=sql_risk_flags,
                            )
                            execution = repaired_execution
            self._raise_if_cancelled(cancellation_token, stage="execution")
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
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="execution",
                status=("completed" if execution else "skipped"),
                detail=(execution.status if execution else "execution skipped"),
                metadata={"row_count": execution.row_count if execution else None},
            )

            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="answer_building",
                status="running",
                detail="building answer",
            )
            answer = self.answer_builder.build(
                classification=classification,
                query_plan=query_plan,
                execution=execution,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                user_context=request.user_context,
            )
            self._raise_if_cancelled(cancellation_token, stage="answer building")
            self._log_stage_io(
                "answer_building",
                inputs={
                    "classification": self._classification_summary(classification),
                    "execution": self._execution_summary(execution),
                    "plan_valid": plan_validation.valid if plan_validation else None,
                    "sql_valid": sql_validation.valid if sql_validation else None,
                },
                outputs={
                    "answer_status": answer.status if answer else None,
                    "summary": self._preview_text(answer.summary if answer else None),
                },
            )
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="answer_building",
                status="completed",
                detail=answer.status if answer else "unknown",
            )

            next_session_state = self.session_state_service.build_next_state(
                query_plan=query_plan,
                previous_state=session_state,
                sql=sql,
            )
            if request.session_id:
                next_session_state.session_id = request.session_id
            self._log_stage_io(
                "next_session_state",
                inputs={"previous": self._session_state_summary(session_state)},
                outputs={"next": self._session_state_summary(next_session_state)},
            )

            response = ChatResponse(
                classification=classification,
                query_intent=query_intent,
                retrieval=retrieval,
                trace=trace,
                answer=answer,
                query_plan=query_plan,
                sql=sql,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
                next_session_state=next_session_state,
            )
            self._append_response_snapshot(trace, response)
            self._persist_success_artifacts(
                trace=trace,
                request=request,
                response=response,
                warnings=warnings + sql_warnings,
            )
            logger.info(
                "chat completed trace_id=%s answer_status=%s plan_valid=%s sql_valid=%s",
                trace.trace_id,
                answer.status if answer else None,
                plan_validation.valid,
                sql_validation.valid,
            )
            self._publish_progress(
                trace.trace_id,
                event_type="completed",
                stage="completed",
                status=answer.status if answer else "ok",
                detail=answer.summary if answer else None,
                metadata={"response": response.model_dump(mode="json")},
            )
            return response
        except ClientCancelledError as exc:
            self.audit_service.append_step(trace, "cancelled", "cancelled", str(exc))
            self._persist_failure_artifacts(
                trace=trace,
                request=request,
                warnings=warnings + [str(exc)],
                answer_status="cancelled",
                classification=classification,
                retrieval=retrieval,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
                sql=sql,
            )
            self._publish_progress(
                trace.trace_id,
                event_type="failed",
                stage="failed",
                status="cancelled",
                detail=str(exc),
            )
            raise
        except Exception as exc:
            self.audit_service.append_step(trace, "failed", "failed", str(exc))
            self._persist_failure_artifacts(
                trace=trace,
                request=request,
                warnings=warnings + [str(exc)],
                answer_status="error",
                classification=classification,
                retrieval=retrieval,
                plan_validation=plan_validation,
                sql_validation=sql_validation,
                execution=execution,
                sql=sql,
            )
            self._publish_progress(
                trace.trace_id,
                event_type="failed",
                stage="failed",
                status="error",
                detail=str(exc),
            )
            raise
        finally:
            self.progress_service.complete(trace.trace_id)
            clear_trace_id()

    def _publish_progress(
        self,
        trace_id: str,
        *,
        event_type: str,
        stage: str,
        status: str,
        detail: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.progress_service.publish(
            ProgressEvent(
                trace_id=trace_id,
                type=event_type,
                stage=stage,
                status=status,
                detail=detail,
                metadata=metadata or {},
            )
        )

    def _log_stage_io(
        self,
        stage: str,
        *,
        inputs: Mapping[str, object] | None = None,
        outputs: Mapping[str, object] | None = None,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug(
            "stage_io stage=%s inputs=%s outputs=%s",
            stage,
            self._compact_payload(inputs or {}),
            self._compact_payload(outputs or {}),
        )

    def _compact_payload(self, value: object, *, depth: int = 0) -> object:
        if depth >= 4:
            return "..."
        if value is None or isinstance(value, (str, int, float, bool)):
            return self._preview_text(value) if isinstance(value, str) else value
        if hasattr(value, "model_dump"):
            return self._compact_payload(value.model_dump(mode="json"), depth=depth + 1)
        if isinstance(value, Mapping):
            compact = {}
            for key, item in value.items():
                compact_item = self._compact_payload(item, depth=depth + 1)
                if compact_item is not _OMITTED:
                    compact[str(key)] = compact_item
            return compact
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            items = [self._compact_payload(item, depth=depth + 1) for item in list(value)[:8]]
            if len(value) > 8:
                items.append(f"...(+{len(value) - 8})")
            return items
        return self._preview_text(str(value))

    @staticmethod
    def _preview_text(value: str | None, max_length: int = 240) -> str | None:
        if value is None:
            return None
        compact = " ".join(str(value).split())
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3] + "..."

    def _session_state_summary(self, session_state: SessionState | None) -> dict | None:
        if session_state is None:
            return None
        return {
            "session_id": session_state.session_id,
            "subject_domain": session_state.subject_domain,
            "tables": session_state.tables,
            "metrics": session_state.metrics,
            "dimensions": session_state.dimensions,
            "filter_fields": [item.field for item in session_state.filters],
            "has_last_sql": bool(session_state.last_sql),
        }

    @staticmethod
    def _intent_summary(query_intent) -> dict | None:
        if query_intent is None:
            return None
        return {
            "subject_domain": query_intent.subject_domain,
            "metrics": query_intent.matched_metrics,
            "entities": query_intent.matched_entities,
            "dimensions": query_intent.requested_dimensions,
            "filter_fields": [item.field for item in query_intent.filters],
            "time_grain": query_intent.time_context.grain,
            "has_version": bool(query_intent.version_context),
            "follow_up": query_intent.has_follow_up_cue,
        }

    @staticmethod
    def _classification_summary(classification) -> dict | None:
        if classification is None:
            return None
        return {
            "question_type": classification.question_type,
            "subject_domain": classification.subject_domain,
            "need_clarification": classification.need_clarification,
            "inherit_context": classification.inherit_context,
            "reason_code": classification.reason_code,
        }

    @staticmethod
    def _query_plan_summary(query_plan) -> dict | None:
        if query_plan is None:
            return None
        return {
            "question_type": query_plan.question_type,
            "subject_domain": query_plan.subject_domain,
            "tables": query_plan.tables,
            "metrics": query_plan.metrics,
            "dimensions": query_plan.dimensions,
            "filter_fields": [item.field for item in query_plan.filters],
            "join_path": query_plan.join_path,
            "time_grain": query_plan.time_context.grain,
            "limit": query_plan.limit,
            "need_clarification": query_plan.need_clarification,
        }

    @staticmethod
    def _retrieval_summary(retrieval) -> dict | None:
        if retrieval is None:
            return None
        return {
            "domains": retrieval.domains,
            "metrics": retrieval.metrics,
            "terms": retrieval.retrieval_terms,
            "channels": retrieval.retrieval_channels,
            "hit_count": len(retrieval.hits),
            "hit_count_by_source": retrieval.hit_count_by_source,
            "top_hits": [
                {
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "score": round(hit.score, 4),
                    "channel": hit.retrieval_channel,
                }
                for hit in retrieval.hits[:5]
            ],
        }

    @staticmethod
    def _execution_summary(execution) -> dict | None:
        if execution is None:
            return None
        return {
            "executed": execution.executed,
            "status": execution.status,
            "row_count": execution.row_count,
            "columns": execution.columns,
            "elapsed_ms": execution.elapsed_ms,
            "truncated": execution.truncated,
            "error_category": execution.error_category,
            "error_count": len(execution.errors),
            "warning_count": len(execution.warnings),
        }

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
        query_intent,
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
            user_context=request.user_context,
        )
        next_session_state = self._preserved_session_state(session_state, request.session_id)

        response = ChatResponse(
            classification=classification,
            query_intent=query_intent,
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
        self._publish_progress(
            trace.trace_id,
            event_type="completed",
            stage="completed",
            status=answer.status if answer else "ok",
            detail=answer.summary if answer else None,
            metadata={"response": response.model_dump(mode="json")},
        )
        self._append_response_snapshot(trace, response)
        self._persist_success_artifacts(
            trace=trace,
            request=request,
            response=response,
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

    def _persist_success_artifacts(
        self,
        *,
        trace,
        request: PlanRequest,
        response: ChatResponse,
        warnings: list[str],
    ) -> None:
        self.conversation_persistence_service.persist_success(
            trace=trace,
            request=request,
            response=response,
            warnings=warnings,
        )

    def _persist_failure_artifacts(
        self,
        *,
        trace,
        request: PlanRequest,
        warnings: list[str],
        answer_status: str,
        classification,
        retrieval,
        plan_validation: ValidationResponse | None,
        sql_validation: ValidationResponse | None,
        execution,
        sql: str | None,
    ) -> None:
        self.conversation_persistence_service.persist_failure(
            trace=trace,
            request=request,
            warnings=warnings,
            answer_status=answer_status,
            classification=classification,
            retrieval=retrieval,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
            execution=execution,
            sql=sql,
        )

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

    def _raise_if_cancelled(
        self,
        cancellation_token: CancellationToken | None,
        *,
        stage: str,
    ) -> None:
        if cancellation_token is None:
            return
        cancellation_token.raise_if_cancelled(stage=stage)
