from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.app.api.dependencies import get_container
from backend.app.core.container import AppContainer
from backend.app.models.api import ChatRequest


router = APIRouter(prefix="/api/semantic", tags=["semantic"])


@router.get("/summary")
def domain_summary(container: AppContainer = Depends(get_container)) -> dict:
    return container.domain_config_loader.summary()


@router.post("/retrieve-preview")
def retrieve_preview(
    request: ChatRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> dict:
    question_context = container.question_context_service.build(
        question=request.question,
        session_state=request.session_state,
    )
    retrieval = container.retrieval_service.retrieve_text(
        question=question_context.effective_question,
        semantic_brief=question_context.semantic_brief,
        conversation_summary=question_context.conversation_summary,
    )
    return {
        "question_context": question_context.model_dump(),
        "retrieval": retrieval.model_dump(),
    }
