from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from backend.app.api.dependencies import get_container, get_current_user, require_admin_user
from backend.app.core.container import AppContainer
from backend.app.models.admin import (
    ExampleCollectionResponse,
    ExampleMutationResponse,
    MetadataDocument,
    MetadataOverview,
    RuntimeQueryLogCollectionResponse,
    RuntimeQueryLogRecord,
    RuntimeRetrievalLogRecord,
    RuntimeSessionCollectionResponse,
    RuntimeSqlAuditRecord,
    SessionSnapshotRecord,
)
from backend.app.models.auth import (
    AdminPasswordResetRequest,
    DataScopeUpdateRequest,
    FieldVisibilityUpdateRequest,
    RoleRecord,
    RoleUpsertRequest,
    UserContext,
    UserUpsertRequest,
)
from backend.app.models.conversation import SessionHistoryResponse
from backend.app.models.evaluation import (
    EvaluationCase,
    EvaluationCaseCollection,
    EvaluationReplayRequest,
    EvaluationReplayResult,
    EvaluationRunRecord,
    EvaluationRunRequest,
    EvaluationSummary,
)
from backend.app.models.feedback import FeedbackCollectionResponse, FeedbackSummary
from backend.app.models.trace import TraceRecord
from backend.app.config import SEMANTIC_VIEW_DRAFTS_PATH


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_user)],
)


class MetadataUpdateRequest(BaseModel):
    content: dict | list | str


class ExampleUpsertRequest(BaseModel):
    example: dict


class ExampleBulkUpsertRequest(BaseModel):
    examples: list[dict]
    replace_existing: bool = False


class EvaluationCaseUpsertRequest(BaseModel):
    case: dict


class RoleUpdateRequest(RoleUpsertRequest):
    pass


@router.get("/metadata/overview", response_model=MetadataOverview)
def metadata_overview(container: AppContainer = Depends(get_container)) -> MetadataOverview:
    return container.metadata_service.overview()


@router.get("/metadata/documents")
def list_metadata_documents(container: AppContainer = Depends(get_container)) -> dict:
    return {"documents": container.metadata_service.list_documents()}


@router.get("/metadata/documents/{name}", response_model=MetadataDocument)
def get_metadata_document(name: str, container: AppContainer = Depends(get_container)) -> MetadataDocument:
    try:
        return container.metadata_service.get_document(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="metadata document not found") from exc


@router.put("/metadata/documents/{name}", response_model=MetadataDocument)
def update_metadata_document(
    name: str,
    request: MetadataUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> MetadataDocument:
    try:
        return container.metadata_service.update_document(name, request.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="metadata document not found") from exc


@router.post("/metadata/reload")
def reload_metadata(container: AppContainer = Depends(get_container)) -> dict:
    return container.metadata_service.reload_runtime(retrieval_service=container.retrieval_service)


@router.get("/examples", response_model=ExampleCollectionResponse)
def list_examples(container: AppContainer = Depends(get_container)) -> ExampleCollectionResponse:
    return container.metadata_service.list_examples(container.retrieval_service)


@router.post("/examples", response_model=ExampleMutationResponse)
def create_example(
    request: ExampleUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleMutationResponse:
    try:
        return container.metadata_service.create_example(
            request.example,
            retrieval_service=container.retrieval_service,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/examples/{example_id}", response_model=ExampleMutationResponse)
def update_example(
    example_id: str,
    request: ExampleUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleMutationResponse:
    try:
        return container.metadata_service.update_example(
            example_id,
            request.example,
            retrieval_service=container.retrieval_service,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="example not found") from exc


@router.post("/examples/bulk", response_model=ExampleCollectionResponse)
def bulk_upsert_examples(
    request: ExampleBulkUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleCollectionResponse:
    try:
        return container.metadata_service.bulk_upsert_examples(
            request.examples,
            retrieval_service=container.retrieval_service,
            replace_existing=request.replace_existing,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/traces", response_model=list[TraceRecord])
def list_traces(container: AppContainer = Depends(get_container)) -> list[TraceRecord]:
    return container.audit_repository.list_records()


@router.get("/traces/{trace_id}", response_model=TraceRecord)
def get_trace(trace_id: str, container: AppContainer = Depends(get_container)) -> TraceRecord:
    trace = container.audit_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace


@router.get("/feedbacks", response_model=FeedbackCollectionResponse)
def list_feedbacks(
    session_id: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    container: AppContainer = Depends(get_container),
) -> FeedbackCollectionResponse:
    return container.feedback_service.list_records(
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        limit=limit,
    )


@router.get("/feedbacks/summary", response_model=FeedbackSummary)
def summarize_feedbacks(
    session_id: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    container: AppContainer = Depends(get_container),
) -> FeedbackSummary:
    return container.feedback_service.summarize(
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        limit=limit,
    )


@router.get("/runtime/status")
def runtime_status(container: AppContainer = Depends(get_container)) -> dict:
    return {
        "business_database": container.business_database_connector.test_connection(),
        "runtime_database": container.runtime_database_connector.test_connection(),
        "llm": container.llm_client.health(),
        "vector_retrieval": container.vector_retriever.health(),
        "retrieval_corpus": container.retrieval_service.health(),
        "classification": {
            "llm_enabled": container.settings.classification_llm_enabled,
        },
        "sql_ast": container.sql_ast_validator.health(),
    }


@router.get("/runtime/sessions", response_model=RuntimeSessionCollectionResponse)
def list_runtime_sessions(
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> RuntimeSessionCollectionResponse:
    return container.runtime_admin_service.list_sessions(limit=limit)


@router.get("/runtime/sessions/{session_id}/history", response_model=SessionHistoryResponse)
def get_runtime_session_history(
    session_id: str,
    container: AppContainer = Depends(get_container),
) -> SessionHistoryResponse:
    history = container.runtime_admin_service.get_session_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="session not found")
    return history


@router.get("/runtime/sessions/{session_id}/snapshots", response_model=list[SessionSnapshotRecord])
def list_runtime_session_snapshots(
    session_id: str,
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> list[SessionSnapshotRecord]:
    return container.runtime_admin_service.list_session_snapshots(session_id=session_id, limit=limit)


@router.get("/runtime/query-logs", response_model=RuntimeQueryLogCollectionResponse)
def list_runtime_query_logs(
    limit: int = 50,
    session_id: str | None = None,
    user_id: str | None = None,
    container: AppContainer = Depends(get_container),
) -> RuntimeQueryLogCollectionResponse:
    return container.runtime_admin_service.list_query_logs(
        limit=limit,
        session_id=session_id,
        user_id=user_id,
    )


@router.get("/runtime/query-logs/{trace_id}", response_model=RuntimeQueryLogRecord)
def get_runtime_query_log(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> RuntimeQueryLogRecord:
    record = container.runtime_admin_service.get_query_log(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="query log not found")
    return record


@router.get("/runtime/query-logs/{trace_id}/retrieval", response_model=list[RuntimeRetrievalLogRecord])
def list_runtime_retrieval_logs(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> list[RuntimeRetrievalLogRecord]:
    return container.runtime_admin_service.list_retrieval_logs(trace_id)


@router.get("/runtime/query-logs/{trace_id}/sql-audit", response_model=RuntimeSqlAuditRecord)
def get_runtime_sql_audit(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> RuntimeSqlAuditRecord:
    record = container.runtime_admin_service.get_sql_audit(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="sql audit not found")
    return record


@router.post("/runtime/query-logs/{trace_id}/replay", response_model=EvaluationReplayResult)
def replay_runtime_query_log(
    trace_id: str,
    request: EvaluationReplayRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationReplayResult:
    try:
        return container.evaluation_service.replay_trace(trace_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="query log not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/database/bootstrap-semantic-views")
def bootstrap_semantic_views(container: AppContainer = Depends(get_container)) -> dict:
    sql_script = SEMANTIC_VIEW_DRAFTS_PATH.read_text(encoding="utf-8")
    return container.business_database_connector.execute_script(sql_script)


@router.get("/users", response_model=list[UserContext])
def list_users(container: AppContainer = Depends(get_container)) -> list[UserContext]:
    return container.auth_service.list_users()


@router.get("/users/{user_id}", response_model=UserContext)
def get_user(
    user_id: str,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    user = container.auth_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.put("/users/{user_id}", response_model=UserContext)
def upsert_user(
    user_id: str,
    request: UserUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    return container.auth_service.upsert_user(user_id, request)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    request: AdminPasswordResetRequest,
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        container.auth_service.admin_reset_password(user_id, request)
        return {"updated": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        container.auth_service.delete_user(current_user, user_id)
        return {"deleted": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/users/{user_id}/data-scope", response_model=UserContext)
def update_user_data_scope(
    user_id: str,
    request: DataScopeUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    try:
        return container.auth_service.update_data_scope(user_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc


@router.put("/users/{user_id}/field-visibility", response_model=UserContext)
def update_user_field_visibility(
    user_id: str,
    request: FieldVisibilityUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    try:
        return container.auth_service.update_field_visibility(user_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc


@router.get("/roles", response_model=list[RoleRecord])
def list_roles(container: AppContainer = Depends(get_container)) -> list[RoleRecord]:
    return container.auth_service.list_roles()


@router.put("/roles/{role_name}", response_model=RoleRecord)
def upsert_role(
    role_name: str,
    request: RoleUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> RoleRecord:
    return container.auth_service.upsert_role(role_name, request)


@router.get("/eval/cases", response_model=EvaluationCaseCollection)
def list_evaluation_cases(container: AppContainer = Depends(get_container)) -> EvaluationCaseCollection:
    return container.evaluation_service.list_cases()


@router.post("/eval/cases", response_model=EvaluationCase)
def create_evaluation_case(
    request: EvaluationCaseUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationCase:
    return container.evaluation_service.create_case(request.case)


@router.post("/eval/cases/{case_id}/replay", response_model=EvaluationReplayResult)
def replay_evaluation_case(
    case_id: str,
    request: EvaluationReplayRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationReplayResult:
    try:
        return container.evaluation_service.replay_case(case_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evaluation case not found") from exc


@router.get("/eval/runs", response_model=list[EvaluationRunRecord])
def list_evaluation_runs(container: AppContainer = Depends(get_container)) -> list[EvaluationRunRecord]:
    return container.evaluation_service.list_runs()


@router.get("/eval/summary", response_model=EvaluationSummary)
def get_evaluation_summary(
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> EvaluationSummary:
    return container.evaluation_service.summarize_runs(limit=limit)


@router.post("/eval/run", response_model=EvaluationRunRecord)
def run_evaluation(
    request: EvaluationRunRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationRunRecord:
    return container.evaluation_service.run(request)
