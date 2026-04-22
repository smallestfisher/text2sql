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
    semantic_parse, classification, warnings = container.query_planner.classify(
        question=request.question,
        session_state=request.session_state,
    )
    return ClassificationResponse(
        classification=classification,
        semantic_parse=semantic_parse,
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
    semantic_parse, classification, query_plan, warnings = container.query_planner.create_plan(
        question=request.question,
        session_state=request.session_state,
    )
    query_plan, permission_warnings = container.permission_service.apply_to_query_plan(
        query_plan=query_plan,
        user_context=request.user_context,
    )
    return PlanResponse(
        classification=classification,
        semantic_parse=semantic_parse,
        query_plan=query_plan,
        semantic_summary=container.semantic_loader.summary(),
        warnings=warnings + permission_warnings,
    )


@router.post("/plan/validate", response_model=ValidationResponse)
def validate_query_plan(
    request: PlanValidationRequest,
    container: AppContainer = Depends(get_container),
) -> ValidationResponse:
    errors, warnings = container.query_plan_validator.validate(
        query_plan=request.query_plan,
        semantic_layer=container.semantic_layer,
    )
    return ValidationResponse(valid=not errors, errors=errors, warnings=warnings)


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
    query_plan, permission_warnings = container.permission_service.apply_to_query_plan(
        query_plan=request.query_plan,
        user_context=request.user_context,
    )
    plan_errors, plan_warnings = container.query_plan_validator.validate(
        query_plan=query_plan,
        semantic_layer=container.semantic_layer,
    )
    generated_sql = None if plan_errors else container.sql_generator.generate(query_plan)
    visible_sql = (
        generated_sql if container.permission_service.can_view_sql(request.user_context) else None
    )
    required_filter_fields = container.permission_service.required_filter_fields(
        query_plan=query_plan,
        user_context=request.user_context,
    )
    sql_errors, sql_warnings = (
        (["sql is empty"], [])
        if generated_sql is None and not plan_errors
        else (
            container.sql_validator.validate(
                generated_sql,
                container.semantic_layer,
                query_plan=query_plan,
                required_filter_fields=required_filter_fields,
            )
            if generated_sql is not None
            else ([], [])
        )
    )
    validation = ValidationResponse(
        valid=not (plan_errors or sql_errors),
        errors=plan_errors + sql_errors,
        warnings=permission_warnings + plan_warnings + sql_warnings,
    )
    return SqlResponse(query_plan=query_plan, sql=visible_sql, validation=validation)


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
    sql_errors, sql_warnings = container.sql_validator.validate(
        request.sql,
        container.semantic_layer,
    )
    if sql_errors:
        return ExecutionResponse(
            executed=False,
            status="db_error",
            sql=request.sql if container.permission_service.can_view_sql(request.user_context) else None,
            row_count=0,
            columns=[],
            rows=[],
            errors=sql_errors,
            warnings=sql_warnings,
            elapsed_ms=None,
            error_category="validation",
            truncated=False,
        )
    execution = container.sql_executor.execute(sql=request.sql, user_context=request.user_context)
    execution = container.permission_service.apply_to_execution(
        execution=execution,
        user_context=request.user_context,
    )
    execution.warnings.extend(sql_warnings)
    if not container.permission_service.can_view_sql(request.user_context):
        execution.sql = None
    return execution
