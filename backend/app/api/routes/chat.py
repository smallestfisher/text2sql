from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.app.api.dependencies import get_container, resolve_request_user_context
from backend.app.core.container import AppContainer
from backend.app.models.api import ChatResponse, PlanRequest
from backend.app.models.feedback import FeedbackRecord, FeedbackRequest


router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
def chat_query(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> ChatResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    return container.orchestrator.chat(request)


@router.post("/feedback", response_model=FeedbackRecord)
def submit_feedback(
    request: FeedbackRequest,
    container: AppContainer = Depends(get_container),
) -> FeedbackRecord:
    return container.feedback_service.submit(request)
