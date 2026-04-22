from __future__ import annotations

from backend.app.models.api import ChatResponse, PlanRequest, ValidationResponse
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
from backend.app.services.sql_generator import SqlGenerator
from backend.app.services.sql_validator import SqlValidator


class ConversationOrchestrator:
    def __init__(
        self,
        query_planner: QueryPlanner,
        query_plan_validator: QueryPlanValidator,
        permission_service: PermissionService,
        query_plan_compiler: QueryPlanCompiler,
        session_state_service: SessionStateService,
        sql_generator: SqlGenerator,
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
        self.sql_generator = sql_generator
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
        warnings: list[str] = []

        session_state = request.session_state
        if request.session_id and session_state is None:
            session_state = self.session_service.resolve_state(request.session_id)
        self.audit_service.append_step(trace, "load_session", "completed", "session state resolved")

        semantic_parse, classification, query_plan, planning_warnings = self.query_planner.create_plan(
            question=request.question,
            session_state=session_state,
        )
        warnings.extend(planning_warnings)
        self.audit_service.append_step(trace, "plan", "completed", classification.question_type)

        retrieval = self.retrieval_service.retrieve(semantic_parse)
        retrieval_summary = self.retrieval_service.summarize_retrieval(retrieval)
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
        if llm_plan_hint.get("mode") == "live":
            candidate_query_plan = self.query_plan_compiler.apply_llm_hint(query_plan, llm_plan_hint)
            acceptable, rejection_reasons = self.query_plan_compiler.semantic_runtime.llm_plan_is_acceptable(
                candidate_query_plan,
                base_query_plan,
            )
            if acceptable:
                query_plan = candidate_query_plan
            else:
                warnings.append(
                    "llm query plan hint rejected before compile: " + "; ".join(rejection_reasons)
                )
        self.audit_service.append_step(
            trace,
            "build_query_plan_prompt",
            "completed",
            "live llm hint applied" if llm_plan_hint.get("mode") == "live" else "stub prompt built",
        )

        query_plan, permission_warnings = self.permission_service.apply_to_query_plan(
            query_plan=query_plan,
            user_context=request.user_context,
        )
        warnings.extend(permission_warnings)
        self.audit_service.append_step(trace, "authorize", "completed", "permission filters applied")

        query_plan = self.query_plan_compiler.compile(query_plan=query_plan, retrieval=retrieval)
        self.audit_service.append_step(trace, "compile_plan", "completed", "query plan compiled")

        plan_errors, plan_warnings = self.query_plan_validator.validate(
            query_plan=query_plan,
            semantic_layer=self.semantic_layer,
        )
        if plan_errors and llm_plan_hint.get("mode") == "live":
            fallback_plan, fallback_permission_warnings = self.permission_service.apply_to_query_plan(
                query_plan=base_query_plan.model_copy(deep=True),
                user_context=request.user_context,
            )
            fallback_plan = self.query_plan_compiler.compile(query_plan=fallback_plan, retrieval=retrieval)
            fallback_errors, fallback_warnings = self.query_plan_validator.validate(
                query_plan=fallback_plan,
                semantic_layer=self.semantic_layer,
            )
            if not fallback_errors:
                warnings.append("llm query plan hint rejected; fallback to local planner result")
                query_plan = fallback_plan
                permission_warnings = fallback_permission_warnings
                plan_errors = []
                plan_warnings = fallback_warnings
        warnings.extend(plan_warnings)
        self.audit_service.append_step(trace, "validate_plan", "completed" if not plan_errors else "failed")

        llm_sql = None
        if not plan_errors:
            sql_prompt = self.prompt_builder.build_sql_prompt(query_plan)
            llm_sql = self.llm_client.generate_sql_hint(sql_prompt)
            if llm_sql:
                self.audit_service.append_step(trace, "build_sql_prompt", "completed", "live sql hint applied")
            else:
                self.audit_service.append_step(trace, "build_sql_prompt", "completed", "fallback to local sql generator")

        sql = None
        if not plan_errors:
            sql = self.sql_generator.generate(query_plan, llm_sql=llm_sql)
        visible_sql = sql if self.permission_service.can_view_sql(request.user_context) else None
        self.audit_service.append_step(trace, "generate_sql", "completed" if sql else "skipped")

        required_filter_fields = self.permission_service.required_filter_fields(
            query_plan=query_plan,
            user_context=request.user_context,
        )
        sql_errors, sql_warnings = (["sql is empty"], []) if sql is None and not plan_errors else (
            self.sql_validator.validate(
                sql,
                self.semantic_layer,
                query_plan=query_plan,
                required_filter_fields=required_filter_fields,
            )
            if sql is not None
            else ([], [])
        )
        if sql_errors and llm_sql and not plan_errors:
            local_sql = self.sql_generator.generate(query_plan, llm_sql=None)
            local_sql_errors, local_sql_warnings = (
                self.sql_validator.validate(
                    local_sql,
                    self.semantic_layer,
                    query_plan=query_plan,
                    required_filter_fields=required_filter_fields,
                )
                if local_sql is not None
                else (["sql is empty"], [])
            )
            if not local_sql_errors:
                warnings.append("llm sql hint rejected; fallback to local sql generator")
                sql = local_sql
                visible_sql = sql if self.permission_service.can_view_sql(request.user_context) else None
                sql_errors = []
                sql_warnings = local_sql_warnings
        self.audit_service.append_step(trace, "validate_sql", "completed" if not sql_errors else "failed")

        execution = None if (plan_errors or sql_errors) else self.sql_executor.execute(
            sql=sql,
            user_context=request.user_context,
        )
        execution = self.permission_service.apply_to_execution(
            execution=execution,
            user_context=request.user_context,
        )
        if execution is not None and not self.permission_service.can_view_sql(request.user_context):
            execution.sql = None
        self.audit_service.append_step(trace, "execute", "completed" if execution else "skipped")

        plan_validation = ValidationResponse(
            valid=not plan_errors,
            errors=plan_errors,
            warnings=warnings,
        )
        sql_validation = ValidationResponse(
            valid=not sql_errors,
            errors=sql_errors,
            warnings=sql_warnings,
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
        self.audit_service.finalize(trace, warnings=warnings + sql_warnings)
        self.runtime_log_repository.log_retrieval(trace.trace_id, retrieval)
        self.runtime_log_repository.log_sql_audit(
            trace_id=trace.trace_id,
            sql=sql,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
            execution=execution,
        )
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
            warnings=warnings + sql_warnings,
        )

        return ChatResponse(
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
