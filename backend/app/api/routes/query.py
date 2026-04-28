from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.app.api.dependencies import get_container, resolve_request_user_context
from backend.app.core.container import AppContainer
from backend.app.models.api import (
    ClassificationResponse,
    ExecutionResponse,
    PlanRequest,
    PlanResponse,
    PlanValidationRequest,
    SqlExecutionRequest,
    SqlGenerationRequest,
    SqlResponse,
    ValidationResponse,
)


router = APIRouter(prefix="/api/query", tags=["query"])


def _sync_classification_with_query_plan(classification, query_plan) -> None:
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


def _sql_skip_warning(query_plan) -> str | None:
    if query_plan.question_type == "invalid":
        return "sql generation skipped: query plan is invalid"
    if query_plan.need_clarification:
        return "sql generation skipped: query plan requires clarification"
    return None


@router.post("/classify", response_model=ClassificationResponse)
def classify_query(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> ClassificationResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    query_intent, classification, warnings = container.query_planner.classify(
        question=request.question,
        session_state=request.session_state,
    )
    return ClassificationResponse(
        classification=classification,
        query_intent=query_intent,
        warnings=warnings,
    )


@router.post("/plan", response_model=PlanResponse)
def create_query_plan(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> PlanResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    query_intent, classification, query_plan, warnings = container.query_planner.create_plan(
        question=request.question,
        session_state=request.session_state,
    )
    _sync_classification_with_query_plan(classification, query_plan)
    return PlanResponse(
        classification=classification,
        query_intent=query_intent,
        query_plan=query_plan,
        domain_summary=container.domain_config_loader.summary(),
        warnings=warnings,
    )


@router.post("/plan/validate", response_model=ValidationResponse)
def validate_query_plan(
    request: PlanValidationRequest,
    container: AppContainer = Depends(get_container),
) -> ValidationResponse:
    result = container.query_plan_validator.validate_detailed(
        query_plan=request.query_plan,
        domain_config=container.domain_config,
    )
    return ValidationResponse(
        valid=not result.errors,
        errors=result.errors,
        warnings=result.warnings,
        risk_level=result.risk_level,
        risk_flags=result.risk_flags,
    )


@router.post("/sql", response_model=SqlResponse)
def generate_sql(
    request: SqlGenerationRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> SqlResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    query_plan = request.query_plan
    plan_result = container.query_plan_validator.validate_detailed(
        query_plan=query_plan,
        domain_config=container.domain_config,
    )
    plan_errors = plan_result.errors
    plan_warnings = plan_result.warnings
    sql_prompt = None
    generated_sql = None
    skip_warning = _sql_skip_warning(query_plan)
    if not plan_errors and skip_warning is None:
        sql_prompt = container.prompt_builder.build_sql_prompt(query_plan)
        generated_sql = container.llm_client.generate_sql_hint(sql_prompt)
    required_filter_fields: list[str] = []
    sql_errors: list[str] = []
    sql_warnings: list[str] = []
    sql_risk_level = "low"
    sql_risk_flags: list[str] = []
    if skip_warning is not None:
        sql_errors = [skip_warning]
    if generated_sql is None and not plan_errors and skip_warning is None:
        sql_errors = ["sql is empty"]
    elif generated_sql is not None:
        sql_result = container.sql_validator.validate_detailed(
            generated_sql,
            container.domain_config,
            query_plan=query_plan,
            required_filter_fields=required_filter_fields,
        )
        sql_errors = sql_result.errors
        sql_warnings = sql_result.warnings
        sql_risk_level = sql_result.risk_level
        sql_risk_flags = sql_result.risk_flags
        if sql_errors and sql_prompt is not None:
            repaired_sql = container.llm_client.repair_sql(
                prompt_payload=sql_prompt,
                sql=generated_sql,
                errors=sql_errors,
                warnings=sql_warnings,
            )
            if repaired_sql:
                repaired_result = container.sql_validator.validate_detailed(
                    repaired_sql,
                    container.domain_config,
                    query_plan=query_plan,
                    required_filter_fields=required_filter_fields,
                )
                if not repaired_result.errors:
                    generated_sql = repaired_sql
                    sql_errors = []
                    sql_warnings = repaired_result.warnings
                    sql_risk_level = repaired_result.risk_level
                    sql_risk_flags = repaired_result.risk_flags
                    sql_warnings.append("llm sql repaired after validation failure")
    validation = ValidationResponse(
        valid=not (plan_errors or sql_errors),
        errors=plan_errors + sql_errors,
        warnings=plan_warnings + ([skip_warning] if skip_warning else []) + sql_warnings,
        risk_level=sql_risk_level,
        risk_flags=sql_risk_flags,
    )
    return SqlResponse(query_plan=query_plan, sql=generated_sql, validation=validation)


@router.post("/execute", response_model=ExecutionResponse)
def execute_sql(
    request: SqlExecutionRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> ExecutionResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    sql_result = container.sql_validator.validate_detailed(
        request.sql,
        container.domain_config,
    )
    if sql_result.errors:
        return ExecutionResponse(
            executed=False,
            status="db_error",
            sql=request.sql,
            row_count=0,
            columns=[],
            rows=[],
            errors=sql_result.errors,
            warnings=sql_result.warnings,
            elapsed_ms=None,
            error_category="validation",
            truncated=False,
        )
    execution = container.sql_executor.execute(sql=request.sql, user_context=request.user_context)
    execution.warnings.extend(sql_result.warnings)
    return execution
