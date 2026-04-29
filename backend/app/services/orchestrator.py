from __future__ import annotations

import logging

from backend.app.logging_config import clear_trace_id, set_trace_id
from backend.app.models.api import ChatResponse, PlanRequest, ValidationResponse
from backend.app.models.progress import ProgressEvent
from backend.app.models.session_state import SessionState
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.llm_client import LLMClient
from backend.app.services.progress_service import ProgressService
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
        self.domain_config = domain_config

    def chat(self, request: PlanRequest, trace_id: str | None = None) -> ChatResponse:
        trace = self.audit_service.new_trace(trace_id=trace_id)
        set_trace_id(trace.trace_id)
        try:
            warnings: list[str] = []
            logger.info(
                "chat start trace_id=%s session_id=%s question=%s",
                trace.trace_id,
                request.session_id,
                request.question,
            )
            self._publish_progress(
                trace.trace_id,
                event_type="accepted",
                stage="accepted",
                status="queued",
                detail="request accepted",
            )

            session_state = request.session_state
            self._publish_progress(trace.trace_id, event_type="stage", stage="load_session", status="running", detail="loading session state")
            if request.session_id and session_state is None:
                session_state = self.session_service.resolve_state(request.session_id)
            self.audit_service.append_step(trace, "load_session", "completed", "session state resolved")
            self._publish_progress(trace.trace_id, event_type="stage", stage="load_session", status="completed", detail="session state resolved")

            self._publish_progress(trace.trace_id, event_type="stage", stage="planning", status="running", detail="building query plan")
            planning_trace = self.query_planner.build_planning_trace(
                question=request.question,
                session_state=session_state,
            )
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
            warnings.extend(planning_warnings)
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
            self._publish_progress(trace.trace_id, event_type="stage", stage="planning", status="completed", detail=classification.question_type)
            self._sync_classification_with_query_plan(classification, query_plan)
            terminal_reason = self._terminal_skip_reason(classification, query_plan)
            if terminal_reason is not None:
                self.audit_service.append_step(trace, "terminal_gate", "completed", terminal_reason)
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

            self._publish_progress(trace.trace_id, event_type="stage", stage="retrieval", status="running", detail="retrieving examples and knowledge")
            retrieval = self.retrieval_service.retrieve(query_intent)
            retrieval_summary = self.retrieval_service.summarize_retrieval(retrieval)
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
            self._publish_progress(trace.trace_id, event_type="stage", stage="retrieval", status="completed", detail=f"{len(retrieval.hits)} hits")

            query_plan = self.query_plan_compiler.compile(query_plan=query_plan, retrieval=retrieval)
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
                metadata={
                    "compiled_plan": query_plan.model_dump(mode="json")
                },
            )

            plan_result = self.query_plan_validator.validate_detailed(
                query_plan=query_plan,
                domain_config=self.domain_config,
            )
            plan_errors = plan_result.errors
            plan_warnings = plan_result.warnings
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
                    query_intent=query_intent,
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
            sql_hint_metadata = {"mode": "not_started", "used": False}
            sql_prompt = None
            if not plan_errors:
                self._publish_progress(trace.trace_id, event_type="stage", stage="sql_generation", status="running", detail="generating sql")
                sql_prompt = self.prompt_builder.build_sql_prompt(query_plan, retrieval=retrieval, question=request.question)
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

            sql = None
            if not plan_errors:
                sql = llm_sql
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
            self._publish_progress(trace.trace_id, event_type="stage", stage="sql_generation", status=("completed" if sql else "skipped"), detail=("sql generated" if sql else "sql unavailable"))

            self._publish_progress(trace.trace_id, event_type="stage", stage="sql_validation", status="running", detail="validating sql")
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
            if sql_errors and llm_sql and not plan_errors and sql_prompt is not None:
                if self._should_target_biz_month_shape_repair(query_plan, sql_errors):
                    targeted_repaired_sql = self.llm_client.repair_sql(
                        prompt_payload=sql_prompt,
                        sql=sql,
                        errors=sql_errors,
                        warnings=sql_warnings,
                        repair_focus="missing_biz_month_dimension_shape",
                        extra_constraints=self._biz_month_repair_constraints(sql_prompt),
                        extra_context={
                            "required_dimensions": list(query_plan.dimensions),
                            "shape_contract": sql_prompt.get("shape_contract"),
                        },
                    )
                    if targeted_repaired_sql:
                        logger.info(
                            "targeted biz_month repair candidate trace_id=%s sql_preview=%s errors=%s",
                            trace.trace_id,
                            targeted_repaired_sql[:800],
                            sql_errors,
                        )
                        targeted_sql_result = self.sql_validator.validate_detailed(
                            targeted_repaired_sql,
                            self.domain_config,
                            query_plan=query_plan,
                            required_filter_fields=required_filter_fields,
                        )
                        if not targeted_sql_result.errors:
                            warnings.append("llm sql repaired after biz_month shape validation failure")
                            sql_hint_metadata["repair_used"] = True
                            sql = targeted_repaired_sql
                            sql_errors = []
                            sql_warnings = targeted_sql_result.warnings
                            sql_risk_level = targeted_sql_result.risk_level
                            sql_risk_flags = targeted_sql_result.risk_flags
                repaired_sql = self.llm_client.repair_sql(
                    prompt_payload=sql_prompt,
                    sql=sql,
                    errors=sql_errors,
                    warnings=sql_warnings,
                ) if sql_errors else None
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
            self._publish_progress(trace.trace_id, event_type="stage", stage="sql_validation", status=("completed" if not sql_errors else "failed"), detail=("sql valid" if not sql_errors else "sql validation failed"), metadata={"errors": sql_errors, "warnings": sql_warnings})

            if not (plan_errors or sql_errors):
                self._publish_progress(trace.trace_id, event_type="stage", stage="execution", status="running", detail="executing sql")
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
                        self.domain_config,
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
                            sql_errors = []
                            sql_warnings = repaired_sql_result.warnings
                            sql_risk_level = repaired_sql_result.risk_level
                            sql_risk_flags = repaired_sql_result.risk_flags
                            execution = repaired_execution
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
            self._publish_progress(trace.trace_id, event_type="stage", stage="execution", status=("completed" if execution else "skipped"), detail=(execution.status if execution else "execution skipped"), metadata={"row_count": execution.row_count if execution else None})

            self._publish_progress(trace.trace_id, event_type="stage", stage="answer_building", status="running", detail="building answer")
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
                user_context=request.user_context,
            )

            self._publish_progress(trace.trace_id, event_type="stage", stage="answer_building", status="completed", detail=answer.status if answer else "unknown")
            next_session_state = self.session_state_service.build_next_state(
                query_plan=query_plan,
                previous_state=session_state,
                sql=sql,
            )

            if request.session_id:
                self.session_service.append_user_message(request.session_id, request.question, trace.trace_id)
                assistant_text = answer.summary
                self.session_service.append_assistant_message(request.session_id, assistant_text, trace.trace_id)
                next_session_state.session_id = request.session_id
                self.session_service.update_state(request.session_id, next_session_state, trace_id=trace.trace_id)
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
            self._publish_progress(trace.trace_id, event_type="completed", stage="completed", status=answer.status if answer else "ok", detail=answer.summary if answer else None, metadata={"response": response.model_dump(mode="json")})
            return response
        except Exception as exc:
            self._publish_progress(trace.trace_id, event_type="failed", stage="failed", status="error", detail=str(exc))
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

    def _should_target_biz_month_shape_repair(self, query_plan, sql_errors: list[str]) -> bool:
        if "biz_month" not in query_plan.dimensions:
            return False
        target_errors = (
            "sql does not group by required dimensions from query plan: biz_month",
            "sql does not project required dimensions from query plan: biz_month",
        )
        return any(error in target_errors for error in sql_errors)

    def _biz_month_repair_constraints(self, sql_prompt: dict) -> list[str]:
        constraints = [
            "本次修复目标是补齐缺失的 biz_month 维度，不要只保留其他分类维度的聚合结果。",
            "最终外层 SELECT 必须显式产出别名 biz_month。",
            "如果存在聚合指标，最终外层 GROUP BY 必须包含 biz_month；优先直接写 GROUP BY biz_month。",
            "若 biz_month 来自日字段，必须先映射到月表达式再别名成 biz_month，不能直接返回日粒度。",
        ]
        shape_contract = sql_prompt.get("shape_contract", {})
        logical_examples = shape_contract.get("logical_dimension_examples", {})
        biz_month_examples = logical_examples.get("biz_month", []) if isinstance(logical_examples, dict) else []
        if biz_month_examples:
            constraints.append(
                "biz_month 可参考这些合法投影形状之一: " + " ; ".join(biz_month_examples[:3])
            )
        return constraints

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

        if request.session_id:
            self.session_service.append_user_message(request.session_id, request.question, trace.trace_id)
            self.session_service.append_assistant_message(request.session_id, answer.summary, trace.trace_id)
            next_session_state.session_id = request.session_id
            self.session_service.update_state(request.session_id, next_session_state, trace_id=trace.trace_id)

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
