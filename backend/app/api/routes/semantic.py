from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.app.api.dependencies import get_container
from backend.app.core.container import AppContainer
from backend.app.models.api import PlanRequest


router = APIRouter(prefix="/api/semantic", tags=["semantic"])


@router.get("/summary")
def domain_summary(container: AppContainer = Depends(get_container)) -> dict:
    return container.domain_config_loader.summary()


@router.post("/retrieve-preview")
def retrieve_preview(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> dict:
    query_intent = container.query_planner.parser.parse(
        question=request.question,
        session_state=request.session_state,
    )
    retrieval = container.retrieval_service.retrieve(query_intent)
    return {
        "query_intent": query_intent.model_dump(),
        "retrieval": retrieval.model_dump(),
        "session_semantic_diff": container.semantic_runtime.session_semantic_diff(
            query_intent,
            request.session_state,
        ),
    }
