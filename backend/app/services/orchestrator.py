from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import time

from backend.app.core.cancellation import CancellationToken
from backend.app.core.exceptions import ClientCancelledError
from backend.app.logging_config import clear_trace_id, set_trace_id
from backend.app.models.api import ChatResponse, ChatRequest, ValidationResponse
from backend.app.models.context_summary import ContextSummary
from backend.app.models.progress import ProgressEvent
from backend.app.models.session_state import PendingClarification, SessionState
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.conversation_persistence_service import ConversationPersistenceService
from backend.app.services.llm_client import LLMClient
from backend.app.services.progress_service import ProgressService
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_analysis_service import QuestionAnalysisService
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.session_service import SessionService
from backend.app.services.session_state_service import SessionStateService
from backend.app.services.sql_executor import SqlExecutor
from backend.app.services.sql_validator import SqlValidator


logger = logging.getLogger(__name__)
_OMITTED = object()
SUPPORTED_SUBJECT_DOMAINS = {"inventory", "demand", "plan_actual", "sales_financial", "dimension", "unknown"}


class ConversationOrchestrator:
    def __init__(
        self,
        question_analysis_service: QuestionAnalysisService,
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
        self.question_analysis_service = question_analysis_service
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
        request: ChatRequest,
        trace_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> ChatResponse:
        trace = self.audit_service.new_trace(trace_id=trace_id)
        set_trace_id(trace.trace_id)

        warnings: list[str] = []
        session_state = request.session_state
        question_context = None
        classification = None
        sql_context = None
        retrieval = None
        sql = None
        execution = None
        context_validation: ValidationResponse | None = None
        sql_validation: ValidationResponse | None = None

        try:
            chat_started_at = time.perf_counter()
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
            stage_started_at = time.perf_counter()
            if request.session_id and session_state is None:
                session_state = self.session_service.resolve_state(request.session_id)
            self._log_timing(trace.trace_id, "load_session", stage_started_at)
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
                stage="question_analysis",
                status="running",
                detail="building question context",
            )
            stage_started_at = time.perf_counter()
            analysis_trace = self.question_analysis_service.analyze_question(
                question=request.question,
                session_state=session_state,
                cancellation_token=cancellation_token,
            )
            self._log_timing(trace.trace_id, "question_analysis", stage_started_at)
            self._raise_if_cancelled(cancellation_token, stage="question_analysis")
            classification = analysis_trace["classification"]
            analysis_warnings = analysis_trace["warnings"]
            effective_question = analysis_trace.get("effective_question") or request.question
            question_context = analysis_trace["question_context"]
            warnings.extend(analysis_warnings)

            self._log_stage_io(
                "question_analysis",
                inputs={
                    "question": request.question,
                    "effective_question": effective_question,
                    "session_state": self._session_state_summary(session_state),
                },
                outputs={
                    "question_context": self._question_context_summary(question_context),
                    "classification": self._classification_summary(classification),
                    "warnings": analysis_warnings,
                },
            )

            self.audit_service.append_step(
                trace,
                "question_context",
                "completed",
                getattr(question_context, "decision", "answerable"),
                metadata={"question_context": question_context.model_dump(mode="json")},
            )

            stage_started_at = time.perf_counter()
            sql_context = self.question_analysis_service.build_sql_context(
                classification=classification,
                session_state=session_state,
                question_context=question_context,
            )
            self._log_timing(trace.trace_id, "sql_context", stage_started_at)
            self._log_stage_io(
                "sql_context",
                inputs={
                    "classification": self._classification_summary(classification),
                },
                outputs={"sql_context": self._sql_context_summary(sql_context)},
            )
            self.audit_service.append_step(
                trace,
                "classification",
                "completed",
                classification.question_type,
                metadata={
                    "classification": classification.model_dump(),
                    "sql_context_summary": {
                        "subject_domain": sql_context.subject_domain,
                        "tables": sql_context.tables,
                        "metrics": sql_context.metrics,
                        "dimensions": sql_context.dimensions,
                        "filter_fields": [item.field for item in sql_context.filters],
                    },
                },
            )
            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="question_analysis",
                status="completed",
                detail=classification.question_type,
            )
            self._sync_classification_with_sql_context(classification, sql_context)
            terminal_reason = self._pre_retrieval_terminal_skip_reason(classification)
            if terminal_reason is not None:
                self._log_timing(trace.trace_id, "terminal_gate", chat_started_at, reason=terminal_reason)
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
                self._raise_if_cancelled(cancellation_token, stage="terminal response")
                total_elapsed_ms = self._log_timing(trace.trace_id, "chat_total", chat_started_at, terminal=True)
                self.audit_service.append_step(
                    trace,
                    "chat_total",
                    "completed",
                    metadata={"elapsed_ms": total_elapsed_ms, "terminal": True},
                )
                response = self._finalize_terminal_response(
                    trace=trace,
                    request=request,
                    session_state=session_state,
                    question_context=question_context,
                    classification=classification,
                    sql_context=sql_context,
                    warnings=warnings,
                    retrieval=None,
                    terminal_reason=terminal_reason,
                )
                return response

            self._publish_progress(
                trace.trace_id,
                event_type="stage",
                stage="retrieval",
                status="running",
                detail="retrieving examples and knowledge",
            )
            stage_started_at = time.perf_counter()
            retrieval = self.retrieval_service.retrieve_text(
                question=effective_question,
                semantic_brief=getattr(question_context, "semantic_brief", None),
                conversation_summary=getattr(question_context, "conversation_summary", None),
            )
            self._log_timing(
                trace.trace_id,
                "retrieval",
                stage_started_at,
                hit_count=len(retrieval.hits),
            )
            self._raise_if_cancelled(cancellation_token, stage="retrieval")
            retrieval_summary = self.retrieval_service.summarize_retrieval(retrieval)
            self._log_stage_io(
                "retrieval",
                inputs={
                    "question": effective_question,
                    "semantic_brief": getattr(question_context, "semantic_brief", None),
                },
                outputs={"retrieval": self._retrieval_summary(retrieval)},
            )
            logger.info(
                "retrieval trace_id=%s hits=%s terms=%s",
                trace.trace_id,
                len(retrieval.hits),
                retrieval.retrieval_terms,
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

            stage_started_at = time.perf_counter()
            classification, sql_context, question_context = self._apply_retrieval_domain_to_sql_context(
                classification=classification,
                sql_context=sql_context,
                question_context=question_context,
                retrieval=retrieval,
            )
            sql_context = self._apply_retrieval_tables_to_sql_context(sql_context, retrieval)
            classification, sql_context, question_context = self._apply_retrieval_support_to_clarification(
                classification=classification,
                sql_context=sql_context,
                question_context=question_context,
                retrieval=retrieval,
                original_question=request.question,
            )
            self._log_timing(trace.trace_id, "sql_context_tables", stage_started_at)
            self._raise_if_cancelled(cancellation_token, stage="SQL context table selection")
            self._log_stage_io(
                "sql_context_tables",
                inputs={"retrieval": self._retrieval_summary(retrieval)},
                outputs={"sql_context": self._sql_context_summary(sql_context)},
            )
            self.audit_service.append_step(
                trace,
                "sql_context_tables",
                "completed",
                "SQL context populated from retrieval tables",
                metadata={"sql_context": sql_context.model_dump(mode="json")},
            )

            context_errors: list[str] = []
            context_warnings: list[str] = []
            self._log_stage_io(
                "validate_context",
                inputs={"sql_context": self._sql_context_summary(sql_context)},
                outputs={
                    "valid": not context_errors,
                    "errors": context_errors,
                    "warnings": context_warnings,
                    "risk_level": "low",
                    "risk_flags": [],
                },
            )
            logger.info(
                "context validation completed trace_id=%s valid=%s errors=%s warnings=%s",
                trace.trace_id,
                not context_errors,
                len(context_errors),
                len(context_warnings),
            )
            self.audit_service.append_step(
                trace,
                "validate_context",
                "completed",
                metadata={
                    "error_count": len(context_errors),
                    "warning_count": len(context_warnings),
                    "errors": context_errors,
                    "warnings": context_warnings,
                    "reason": "SQL context packaged from retrieved evidence; SQL validator owns executable SQL safety boundaries",
                },
            )
            context_validation = ValidationResponse(
                valid=True,
                errors=[],
                warnings=warnings,
                risk_level="low",
                risk_flags=[],
            )
            self._sync_classification_with_sql_context(classification, sql_context)
            terminal_reason = self._terminal_skip_reason(classification, sql_context)
            if terminal_reason is not None:
                self._log_timing(trace.trace_id, "terminal_gate", chat_started_at, reason=terminal_reason)
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
                self._raise_if_cancelled(cancellation_token, stage="terminal response")
                total_elapsed_ms = self._log_timing(trace.trace_id, "chat_total", chat_started_at, terminal=True)
                self.audit_service.append_step(
                    trace,
                    "chat_total",
                    "completed",
                    metadata={"elapsed_ms": total_elapsed_ms, "terminal": True},
                )
                response = self._finalize_terminal_response(
                    trace=trace,
                    request=request,
                    session_state=session_state,
                    question_context=question_context,
                    classification=classification,
                    sql_context=sql_context,
                    warnings=warnings,
                    retrieval=retrieval,
                    terminal_reason=terminal_reason,
                    context_validation=context_validation,
                )
                return response

            llm_sql = None
            sql_hint_metadata = {"mode": "not_started", "used": False}
            sql_prompt = None
            if not context_errors:
                self._publish_progress(
                    trace.trace_id,
                    event_type="stage",
                    stage="sql_generation",
                    status="running",
                    detail="generating sql",
                )
                stage_started_at = time.perf_counter()
                sql_prompt = self.prompt_builder.build_sql_prompt(
                    sql_context,
                    retrieval=retrieval,
                    question=effective_question,
                )
                self._log_timing(trace.trace_id, "build_sql_prompt", stage_started_at)
                self._log_stage_io(
                    "build_sql_prompt",
                    inputs={
                        "question": effective_question,
                        "original_question": request.question,
                        "sql_context": self._sql_context_summary(sql_context),
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
                stage_started_at = time.perf_counter()
                llm_sql = self.llm_client.generate_sql_hint(
                    sql_prompt,
                    cancellation_token=cancellation_token,
                )
                self._log_timing(
                    trace.trace_id,
                    "generate_sql_hint",
                    stage_started_at,
                    sql_present=bool(llm_sql),
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

            sql = llm_sql
            self._log_stage_io(
                "generate_sql",
                inputs={"llm_enabled": self.llm_client.enabled},
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
                    "used_sources": sql_context.tables,
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
                "sql validation input trace_id=%s sql_present=%s sql_preview=%s available_tables=%s",
                trace.trace_id,
                bool(sql),
                (sql[:800] if sql else None),
                sql_context.tables,
            )
            stage_started_at = time.perf_counter()
            sql_result = (
                self.sql_validator.validate_detailed(
                    sql,
                    self.domain_config,
                    sql_context=sql_context,
                    required_filter_fields=required_filter_fields,
                )
                if sql is not None
                else None
            )
            self._log_timing(trace.trace_id, "validate_sql", stage_started_at)
            sql_errors = ["sql is empty"] if sql is None else (sql_result.errors if sql_result else [])
            sql_warnings = sql_result.warnings if sql_result is not None else []
            sql_risk_level = sql_result.risk_level if sql_result is not None else "low"
            sql_risk_flags = sql_result.risk_flags if sql_result is not None else []
            self._log_stage_io(
                "validate_sql",
                inputs={"sql_preview": self._preview_text(sql)},
                outputs={
                    "valid": not sql_errors,
                    "errors": sql_errors,
                    "warnings": sql_warnings,
                    "risk_level": sql_risk_level,
                    "risk_flags": sql_risk_flags,
                },
            )
            validation_repair_allowed, validation_repair_reason = self._should_repair_validation_errors(
                errors=sql_errors,
                sql=sql,
                llm_sql=llm_sql,
                context_errors=[],
                sql_prompt=sql_prompt,
            )
            if validation_repair_allowed:
                stage_started_at = time.perf_counter()
                repaired_sql = self.llm_client.repair_sql(
                    prompt_payload=sql_prompt,
                    sql=sql,
                    errors=sql_errors,
                    warnings=sql_warnings,
                    cancellation_token=cancellation_token,
                )
                self._log_timing(
                    trace.trace_id,
                    "repair_sql_after_validation",
                    stage_started_at,
                    repaired=bool(repaired_sql),
                    repair_reason=validation_repair_reason,
                )
                if repaired_sql and sql_errors:
                    logger.info(
                        "sql repair candidate trace_id=%s sql_preview=%s errors=%s",
                        trace.trace_id,
                        repaired_sql[:800],
                        sql_errors,
                    )
                    stage_started_at = time.perf_counter()
                    repaired_sql_result = self.sql_validator.validate_detailed(
                        repaired_sql,
                        self.domain_config,
                        sql_context=sql_context,
                        required_filter_fields=required_filter_fields,
                    )
                    self._log_timing(trace.trace_id, "validate_repaired_sql", stage_started_at)
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
                    "repair_skipped_reason": validation_repair_reason if sql_errors and not validation_repair_allowed else None,
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

            if not sql_errors:
                self._publish_progress(
                    trace.trace_id,
                    event_type="stage",
                    stage="execution",
                    status="running",
                    detail="executing sql",
                )
            stage_started_at = time.perf_counter()
            execution = None if sql_errors else self.sql_executor.execute(
                sql=sql,
                user_context=request.user_context,
                cancellation_token=cancellation_token,
            )
            self._log_timing(
                trace.trace_id,
                "execute_sql",
                stage_started_at,
                skipped=bool(sql_errors),
                status=execution.status if execution else None,
                row_count=execution.row_count if execution else None,
                db_elapsed_ms=execution.elapsed_ms if execution else None,
            )
            self._log_stage_io(
                "execute_sql",
                inputs={"sql_preview": self._preview_text(sql), "blocked_by_errors": bool(sql_errors)},
                outputs={"execution": self._execution_summary(execution)},
            )
            execution_repair_allowed, execution_repair_reason = self._should_repair_execution_failure(
                execution=execution,
                sql=sql,
                llm_sql=llm_sql,
                sql_prompt=sql_prompt,
            )
            if execution_repair_allowed:
                stage_started_at = time.perf_counter()
                repaired_sql = self.llm_client.repair_sql(
                    prompt_payload=sql_prompt,
                    sql=sql,
                    errors=execution.errors,
                    warnings=execution.warnings,
                    cancellation_token=cancellation_token,
                )
                self._log_timing(
                    trace.trace_id,
                    "repair_sql_after_execution",
                    stage_started_at,
                    repaired=bool(repaired_sql),
                    repair_reason=execution_repair_reason,
                )
                if repaired_sql:
                    stage_started_at = time.perf_counter()
                    repaired_sql_result = self.sql_validator.validate_detailed(
                        repaired_sql,
                        self.domain_config,
                        sql_context=sql_context,
                        required_filter_fields=required_filter_fields,
                    )
                    self._log_timing(trace.trace_id, "validate_execution_repaired_sql", stage_started_at)
                    if not repaired_sql_result.errors:
                        stage_started_at = time.perf_counter()
                        repaired_execution = self.sql_executor.execute(
                            sql=repaired_sql,
                            user_context=request.user_context,
                            cancellation_token=cancellation_token,
                        )
                        self._log_timing(
                            trace.trace_id,
                            "execute_repaired_sql",
                            stage_started_at,
                            status=repaired_execution.status,
                            row_count=repaired_execution.row_count,
                            db_elapsed_ms=repaired_execution.elapsed_ms,
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
            stage_started_at = time.perf_counter()
            answer = self.answer_builder.build(
                classification=classification,
                execution=execution,
                context_validation=context_validation,
                sql_validation=sql_validation,
                user_context=request.user_context,
                metrics=list(getattr(sql_context, "metrics", []) or []),
            )
            self._log_timing(trace.trace_id, "answer_building", stage_started_at)
            self._raise_if_cancelled(cancellation_token, stage="answer building")
            self._log_stage_io(
                "answer_building",
                inputs={
                    "classification": self._classification_summary(classification),
                    "execution": self._execution_summary(execution),
                    "context_valid": context_validation.valid if context_validation else None,
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

            stage_started_at = time.perf_counter()
            state_update = self.session_state_service.update_from_sql_context(sql_context)
            next_session_state = self.session_state_service.build_next_state(
                update=state_update,
                previous_state=session_state,
                question=request.question,
                effective_question=effective_question,
                sql=sql,
            )
            self._log_timing(trace.trace_id, "next_session_state", stage_started_at)
            if request.session_id:
                next_session_state.session_id = request.session_id
            self._log_stage_io(
                "next_session_state",
                inputs={"previous": self._session_state_summary(session_state)},
                outputs={"next": self._session_state_summary(next_session_state)},
            )
            total_elapsed_ms = self._log_timing(trace.trace_id, "chat_total", chat_started_at)
            self.audit_service.append_step(
                trace,
                "chat_total",
                "completed",
                metadata={"elapsed_ms": total_elapsed_ms},
            )

            response = ChatResponse(
                question_context=question_context,
                classification=classification,
                context_summary=self._context_summary(
                    classification=classification,
                    sql_context=sql_context,
                    retrieval=retrieval,
                    question_context=question_context,
                ),
                retrieval=retrieval,
                trace=trace,
                answer=answer,
                sql=sql,
                context_validation=context_validation,
                sql_validation=sql_validation,
                execution=execution,
                next_session_state=next_session_state,
            )
            stage_started_at = time.perf_counter()
            self._append_response_snapshot(trace, response)
            self._persist_success_artifacts(
                trace=trace,
                request=request,
                response=response,
                warnings=warnings + sql_warnings,
            )
            self._log_timing(trace.trace_id, "persist_success", stage_started_at)
            logger.info(
                "chat completed trace_id=%s answer_status=%s context_valid=%s sql_valid=%s",
                trace.trace_id,
                answer.status if answer else None,
                context_validation.valid,
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
                question_context=question_context,
                context_validation=context_validation,
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
                question_context=question_context,
                context_validation=context_validation,
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

    def _log_timing(self, trace_id: str, stage: str, started_at: float, **metadata: object) -> int:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        metadata_parts = " ".join(
            f"{key}={self._preview_text(str(value), max_length=120)}"
            for key, value in metadata.items()
            if value is not None
        )
        suffix = f" {metadata_parts}" if metadata_parts else ""
        logger.info(
            "timing trace_id=%s stage=%s elapsed_ms=%s%s",
            trace_id,
            stage,
            elapsed_ms,
            suffix,
        )
        return elapsed_ms

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
            "semantic_brief": session_state.last_semantic_brief,
            "subject_domain": session_state.subject_domain,
            "tables": session_state.tables,
            "metrics": session_state.metrics,
            "dimensions": session_state.dimensions,
            "filter_fields": [item.field for item in session_state.filters],
            "has_last_sql": bool(session_state.last_sql),
        }

    def _should_repair_validation_errors(
        self,
        *,
        errors: list[str],
        sql: str | None,
        llm_sql: str | None,
        context_errors: list[str],
        sql_prompt: dict | None,
    ) -> tuple[bool, str]:
        if not errors:
            return False, "no_validation_errors"
        if sql is None:
            return False, "sql_missing"
        if not llm_sql:
            return False, "sql_not_from_llm"
        if context_errors:
            return False, "context_has_errors"
        if sql_prompt is None:
            return False, "sql_prompt_missing"
        non_repairable = [error for error in errors if not self._validation_error_is_repairable(error)]
        if non_repairable:
            return False, "non_repairable_validation_error"
        return True, "repairable_validation_error"

    def _validation_error_is_repairable(self, error: str) -> bool:
        normalized = error.lower()
        if "sql is empty" in normalized:
            return False
        if "forbidden keyword detected" in normalized:
            return False
        repairable_markers = (
            "only select statements are allowed",
            "multiple sql statements are not allowed",
            "sql references unknown sources",
            "sql is missing required permission filters",
            "incompatible time literals",
        )
        return any(marker in normalized for marker in repairable_markers)

    def _should_repair_execution_failure(
        self,
        *,
        execution,
        sql: str | None,
        llm_sql: str | None,
        sql_prompt: dict | None,
    ) -> tuple[bool, str]:
        if execution is None:
            return False, "execution_missing"
        if execution.executed:
            return False, "execution_succeeded"
        if sql is None:
            return False, "sql_missing"
        if not llm_sql:
            return False, "sql_not_from_llm"
        if sql_prompt is None:
            return False, "sql_prompt_missing"
        if not self.llm_client.enabled:
            return False, "llm_disabled"
        if execution.error_category in {"governance", "permission", "auth"}:
            return False, "non_repairable_execution_category"
        if not execution.errors:
            return False, "execution_error_missing"
        if not any(self._execution_error_is_repairable(error) for error in execution.errors):
            return False, "non_repairable_execution_error"
        return True, "repairable_execution_error"

    def _execution_error_is_repairable(self, error: str) -> bool:
        normalized = error.lower()
        non_repairable_markers = (
            "comments are not allowed",
            "for update is not allowed",
            "into outfile is not allowed",
            "maximum length",
            "permission denied",
            "not authorized",
        )
        if any(marker in normalized for marker in non_repairable_markers):
            return False
        repairable_markers = (
            "syntax",
            "invalid identifier",
            "unknown column",
            "unknown table",
            "doesn't exist",
            "does not exist",
            "ambiguous",
            "missing",
            "ora-009",
            "ora-01722",
            "ora-018",
        )
        return any(marker in normalized for marker in repairable_markers)

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
    def _sql_context_summary(sql_context) -> dict | None:
        if sql_context is None:
            return None
        return {
            "semantic_brief": sql_context.semantic_brief,
            "question_type": sql_context.question_type,
            "subject_domain": sql_context.subject_domain,
            "tables": sql_context.tables,
            "metrics": sql_context.metrics,
            "dimensions": sql_context.dimensions,
            "filter_fields": [item.field for item in sql_context.filters],
            "join_path": sql_context.join_path,
            "time_grain": sql_context.time_context.grain,
            "limit": sql_context.limit,
            "need_clarification": sql_context.need_clarification,
        }

    def _context_summary(
        self,
        *,
        classification,
        sql_context,
        retrieval,
        question_context,
    ) -> ContextSummary:
        semantic_brief = (
            getattr(sql_context, "semantic_brief", None)
            or getattr(question_context, "semantic_brief", None)
            or getattr(question_context, "effective_question", None)
        )
        return ContextSummary(
            question_type=getattr(classification, "question_type", None) or getattr(sql_context, "question_type", None),
            subject_domain=(
                getattr(sql_context, "subject_domain", None)
                or getattr(classification, "subject_domain", None)
                or getattr(question_context, "subject_domain", None)
                or "unknown"
            ),
            semantic_brief=semantic_brief,
            tables=list(getattr(sql_context, "tables", []) or []),
            retrieval_domains=list(getattr(retrieval, "domains", []) or []),
            retrieval_metrics=list(getattr(retrieval, "metrics", []) or []),
            limit=getattr(sql_context, "limit", None),
            need_clarification=bool(
                getattr(sql_context, "need_clarification", False)
                or getattr(classification, "need_clarification", False)
            ),
            clarification_question=(
                getattr(sql_context, "clarification_question", None)
                or getattr(classification, "clarification_question", None)
                or getattr(question_context, "clarification_question", None)
            ),
        )

    @staticmethod
    def _question_context_summary(question_context) -> dict | None:
        if question_context is None:
            return None
        return {
            "decision": getattr(question_context, "decision", None),
            "context_relation": getattr(question_context, "context_relation", None),
            "effective_question": getattr(question_context, "effective_question", None),
            "subject_domain": getattr(question_context, "subject_domain", None),
            "semantic_brief": getattr(question_context, "semantic_brief", None),
            "source": getattr(question_context, "source", None),
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

    def _sync_classification_with_sql_context(self, classification, sql_context) -> None:
        if classification.question_type == "invalid":
            return
        if not sql_context.need_clarification:
            return
        sql_context.question_type = "clarification_needed"
        classification.question_type = "clarification_needed"
        classification.need_clarification = True
        classification.inherit_context = False
        classification.reason = sql_context.reason or classification.reason
        classification.reason_code = sql_context.reason_code or classification.reason_code
        classification.clarification_question = (
            sql_context.clarification_question
            or classification.clarification_question
            or sql_context.reason
            or "请补充查询目标、时间范围或统计口径。"
        )

    def _apply_retrieval_tables_to_sql_context(self, sql_context, retrieval):
        if retrieval is None:
            return sql_context
        tables = list(sql_context.tables)
        for hit in retrieval.hits:
            if not self._hit_matches_domain(hit, sql_context.subject_domain):
                continue
            metadata_tables = hit.metadata.get("tables", [])
            if isinstance(metadata_tables, list):
                for table_name in metadata_tables:
                    if isinstance(table_name, str) and table_name and table_name not in tables:
                        tables.append(table_name)
            table = hit.metadata.get("table")
            if isinstance(table, str) and table and table not in tables:
                tables.append(table)
        if tables == sql_context.tables:
            return sql_context
        return sql_context.model_copy(deep=True, update={"tables": tables[:8]})

    def _apply_retrieval_domain_to_sql_context(self, *, classification, sql_context, question_context, retrieval):
        resolved_domain = self._first_known_domain(
            sql_context.subject_domain,
            classification.subject_domain,
            getattr(question_context, "subject_domain", None) if question_context is not None else None,
        )
        if resolved_domain is None and retrieval is not None:
            resolved_domain = self._single_retrieval_domain(retrieval)
        if resolved_domain is None:
            return classification, sql_context, question_context

        logger.info(
            "domain resolved subject_domain=%s retrieval_domains=%s",
            resolved_domain,
            retrieval.domains if retrieval is not None else [],
        )
        if classification.subject_domain == "unknown":
            classification = classification.model_copy(update={"subject_domain": resolved_domain})
        if sql_context.subject_domain == "unknown":
            sql_context = sql_context.model_copy(update={
                "subject_domain": resolved_domain,
                "limit": self.question_analysis_service.semantic_runtime.default_limit(resolved_domain),
            })
        if question_context is not None and getattr(question_context, "subject_domain", "unknown") == "unknown":
            question_context = question_context.model_copy(update={"subject_domain": resolved_domain})
        return classification, sql_context, question_context

    def _first_known_domain(self, *values: str | None) -> str | None:
        for value in values:
            normalized = (value or "").strip()
            if normalized in SUPPORTED_SUBJECT_DOMAINS and normalized != "unknown":
                return normalized
        return None

    def _single_retrieval_domain(self, retrieval) -> str | None:
        for hit in getattr(retrieval, "hits", []) or []:
            hit_domain = self._single_primary_domain(self._hit_domains(hit))
            if hit_domain is not None:
                return hit_domain
        domains = [domain for domain in retrieval.domains if domain in SUPPORTED_SUBJECT_DOMAINS and domain != "unknown"]
        unique_domains = []
        for domain in domains:
            if domain not in unique_domains:
                unique_domains.append(domain)
        resolved_domain = self._single_primary_domain(unique_domains)
        if resolved_domain is not None:
            return resolved_domain

        table_domains = []
        for hit in retrieval.hits:
            table_names = []
            metadata_tables = hit.metadata.get("tables", [])
            if isinstance(metadata_tables, list):
                table_names.extend(table for table in metadata_tables if isinstance(table, str))
            metadata_table = hit.metadata.get("table")
            if isinstance(metadata_table, str):
                table_names.append(metadata_table)
            for table_name in table_names:
                for domain in self.question_analysis_service.semantic_runtime.table_domains(table_name):
                    if domain != "unknown" and domain not in table_domains:
                        table_domains.append(domain)
        return self._single_primary_domain(table_domains)

    def _hit_matches_domain(self, hit, subject_domain: str) -> bool:
        if subject_domain == "unknown":
            return True
        domains = self._hit_domains(hit)
        if not domains:
            return True
        return subject_domain in domains or (domains == ["dimension"])

    def _hit_domains(self, hit) -> list[str]:
        domains: list[str] = []
        metadata = getattr(hit, "metadata", {}) or {}
        metadata_domains = metadata.get("domains", [])
        if isinstance(metadata_domains, list):
            for domain_name in metadata_domains:
                if domain_name in SUPPORTED_SUBJECT_DOMAINS and domain_name != "unknown" and domain_name not in domains:
                    domains.append(domain_name)
        subject_domain = metadata.get("subject_domain")
        if subject_domain in SUPPORTED_SUBJECT_DOMAINS and subject_domain != "unknown" and subject_domain not in domains:
            domains.append(subject_domain)
        return domains

    def _single_primary_domain(self, domains: list[str]) -> str | None:
        if len(domains) == 1:
            return domains[0]
        primary_domains = [domain for domain in domains if domain != "dimension"]
        if len(primary_domains) == 1:
            return primary_domains[0]
        return None

    def _apply_retrieval_support_to_clarification(
        self,
        *,
        classification,
        sql_context,
        question_context,
        retrieval,
        original_question: str,
    ):
        if not getattr(classification, "need_clarification", False) and not getattr(sql_context, "need_clarification", False):
            return classification, sql_context, question_context
        if not self._retrieval_has_answer_support(retrieval):
            return classification, sql_context, question_context

        logger.info(
            "retrieval support resolved clarification trace_domain=%s hits=%s",
            getattr(sql_context, "subject_domain", None),
            len(getattr(retrieval, "hits", []) or []),
        )
        resolved_question_type = "follow_up" if getattr(question_context, "context_relation", "new") == "follow_up" else "new"
        classification = classification.model_copy(update={
            "question_type": resolved_question_type,
            "need_clarification": False,
            "clarification_question": None,
            "reason_code": "retrieval_supported_answerable",
            "reason": getattr(question_context, "semantic_brief", None) or getattr(classification, "reason", None),
        })
        sql_context = sql_context.model_copy(update={
            "question_type": resolved_question_type,
            "need_clarification": False,
            "clarification_question": None,
            "reason_code": "retrieval_supported_answerable",
        })
        if question_context is not None and getattr(question_context, "decision", "answerable") == "clarification_needed":
            question_context = question_context.model_copy(update={
                "decision": "answerable",
                "effective_question": getattr(question_context, "effective_question", None) or original_question,
                "clarification_question": None,
                "reason": "retrieval supplied semantic support after question-context clarification suggestion",
            })
        return classification, sql_context, question_context

    def _retrieval_has_answer_support(self, retrieval) -> bool:
        if retrieval is None:
            return False
        for hit in getattr(retrieval, "hits", []) or []:
            source_type = getattr(hit, "source_type", "")
            if source_type in {"example", "knowledge", "join_pattern"}:
                return True
            if source_type == "table_schema" and self._single_retrieval_domain(retrieval) is not None:
                return True
        return False

    def _pre_retrieval_terminal_skip_reason(self, classification) -> str | None:
        if classification.question_type == "invalid":
            return "terminal gate: invalid question, skip retrieval and SQL generation"
        return None

    def _terminal_skip_reason(self, classification, sql_context) -> str | None:
        if classification.question_type == "invalid":
            return "terminal gate: invalid question, skip retrieval and SQL generation"
        if classification.need_clarification or sql_context.need_clarification:
            return "terminal gate: clarification required, skip SQL generation"
        return None

    def _finalize_terminal_response(
        self,
        *,
        trace,
        request: ChatRequest,
        session_state: SessionState | None,
        question_context,
        classification,
        sql_context,
        warnings: list[str],
        retrieval,
        terminal_reason: str,
        context_validation: ValidationResponse | None = None,
    ) -> ChatResponse:
        sql_validation = ValidationResponse(
            valid=True,
            errors=[],
            warnings=[terminal_reason],
            risk_level="low",
            risk_flags=[],
        )
        if context_validation is None:
            context_validation = ValidationResponse(
                valid=True,
                errors=[],
                warnings=warnings + [terminal_reason],
                risk_level="low",
                risk_flags=[],
            )
        answer = self.answer_builder.build(
            classification=classification,
            execution=None,
            context_validation=context_validation,
            sql_validation=sql_validation,
            user_context=request.user_context,
            metrics=list(getattr(sql_context, "metrics", []) or []),
        )
        next_session_state = self._terminal_session_state(
            session_state=session_state,
            session_id=request.session_id,
            request=request,
            question_context=question_context,
            classification=classification,
        )

        response = ChatResponse(
            question_context=question_context,
            classification=classification,
            context_summary=self._context_summary(
                classification=classification,
                sql_context=sql_context,
                retrieval=retrieval,
                question_context=question_context,
            ),
            retrieval=retrieval,
            trace=trace,
            answer=answer,
            sql=None,
            context_validation=context_validation,
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

    def _terminal_session_state(
        self,
        *,
        session_state: SessionState | None,
        session_id: str | None,
        request: ChatRequest,
        question_context,
        classification,
    ) -> SessionState:
        state = self._preserved_session_state(session_state, session_id)
        if not getattr(classification, "need_clarification", False):
            return state
        clarification_question = getattr(classification, "clarification_question", None) or getattr(
            question_context,
            "clarification_question",
            None,
        )
        if not clarification_question:
            return state
        state.pending_clarification = PendingClarification(
            original_question=request.question,
            effective_question=getattr(question_context, "effective_question", None) or None,
            semantic_brief=getattr(question_context, "semantic_brief", None) or None,
            clarification_question=clarification_question,
            reason=getattr(question_context, "reason", None) or getattr(classification, "reason", None),
        )
        return state

    def _persist_success_artifacts(
        self,
        *,
        trace,
        request: ChatRequest,
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
        request: ChatRequest,
        warnings: list[str],
        answer_status: str,
        classification,
        retrieval,
        question_context,
        context_validation: ValidationResponse | None,
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
            question_context=question_context,
            context_validation=context_validation,
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
