from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.app.api.dependencies import get_container
from backend.app.core.container import AppContainer
from backend.app.models.api import PlanRequest


router = APIRouter(prefix="/api/semantic", tags=["semantic"])


@router.get("/summary")
def semantic_summary(container: AppContainer = Depends(get_container)) -> dict:
    return container.semantic_loader.summary()


@router.post("/retrieve-preview")
def retrieve_preview(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> dict:
    semantic_parse = container.query_planner.parser.parse(
        question=request.question,
        session_state=request.session_state,
    )
    retrieval = container.retrieval_service.retrieve(semantic_parse)
    return {
        "semantic_parse": semantic_parse.model_dump(),
        "retrieval": retrieval.model_dump(),
        "session_semantic_diff": container.semantic_runtime.session_semantic_diff(
            semantic_parse,
            request.session_state,
        ),
    }
