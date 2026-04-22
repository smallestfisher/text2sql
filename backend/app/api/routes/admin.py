from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from backend.app.api.dependencies import get_container, require_admin_user
from backend.app.core.container import AppContainer
from backend.app.models.admin import (
    ExampleCollectionResponse,
    ExampleMutationResponse,
    MetadataDocument,
    MetadataOverview,
)
from backend.app.models.auth import UserContext, UserUpsertRequest
from backend.app.models.evaluation import EvaluationCase, EvaluationCaseCollection, EvaluationRunRecord, EvaluationRunRequest
from backend.app.models.feedback import FeedbackRecord
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


class EvaluationCaseUpsertRequest(BaseModel):
    case: dict


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


@router.get("/traces", response_model=list[TraceRecord])
def list_traces(container: AppContainer = Depends(get_container)) -> list[TraceRecord]:
    return container.audit_repository.list_records()


@router.get("/traces/{trace_id}", response_model=TraceRecord)
def get_trace(trace_id: str, container: AppContainer = Depends(get_container)) -> TraceRecord:
    trace = container.audit_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace


@router.get("/feedbacks", response_model=list[FeedbackRecord])
def list_feedbacks(container: AppContainer = Depends(get_container)) -> list[FeedbackRecord]:
    return container.feedback_service.list_records()


@router.get("/runtime/status")
def runtime_status(container: AppContainer = Depends(get_container)) -> dict:
    return {
        "database": container.sql_executor.health(),
        "llm": container.llm_client.health(),
    }


@router.post("/database/bootstrap-semantic-views")
def bootstrap_semantic_views(container: AppContainer = Depends(get_container)) -> dict:
    sql_script = SEMANTIC_VIEW_DRAFTS_PATH.read_text(encoding="utf-8")
    return container.database_connector.execute_script(sql_script)


@router.get("/users", response_model=list[UserContext])
def list_users(container: AppContainer = Depends(get_container)) -> list[UserContext]:
    return container.auth_service.list_users()


@router.put("/users/{user_id}", response_model=UserContext)
def upsert_user(
    user_id: str,
    request: UserUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    return container.auth_service.upsert_user(user_id, request)


@router.get("/eval/cases", response_model=EvaluationCaseCollection)
def list_evaluation_cases(container: AppContainer = Depends(get_container)) -> EvaluationCaseCollection:
    return container.evaluation_service.list_cases()


@router.post("/eval/cases", response_model=EvaluationCase)
def create_evaluation_case(
    request: EvaluationCaseUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationCase:
    return container.evaluation_service.create_case(request.case)


@router.get("/eval/runs", response_model=list[EvaluationRunRecord])
def list_evaluation_runs(container: AppContainer = Depends(get_container)) -> list[EvaluationRunRecord]:
    return container.evaluation_service.list_runs()


@router.post("/eval/run", response_model=EvaluationRunRecord)
def run_evaluation(
    request: EvaluationRunRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationRunRecord:
    return container.evaluation_service.run(request)
