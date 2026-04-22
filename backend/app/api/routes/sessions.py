from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.api.dependencies import get_container, resolve_request_user_context
from backend.app.core.container import AppContainer
from backend.app.models.conversation import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionHistoryResponse,
    SessionStateResponse,
)


router = APIRouter(prefix="/api/chat", tags=["sessions"])


@router.post("/sessions", response_model=SessionCreateResponse)
def create_session(
    request: SessionCreateRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> SessionCreateResponse:
    user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    session = container.session_service.create_session(
        user_id=user_context.user_id if user_context else None,
        title=request.title,
    )
    return SessionCreateResponse(session=session)


@router.get("/sessions/{session_id}", response_model=SessionCreateResponse)
def get_session(
    session_id: str,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> SessionCreateResponse:
    session = container.session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    container.session_service.ensure_access(
        session,
        resolve_request_user_context(http_request, container),
    )
    return SessionCreateResponse(session=session)


@router.get("/history/{session_id}", response_model=SessionHistoryResponse)
def get_history(
    session_id: str,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> SessionHistoryResponse:
    session = container.session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    container.session_service.ensure_access(
        session,
        resolve_request_user_context(http_request, container),
    )
    return SessionHistoryResponse(
        session=session,
        messages=container.session_service.history(session_id),
    )


@router.get("/state/{session_id}", response_model=SessionStateResponse)
def get_state(
    session_id: str,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> SessionStateResponse:
    session = container.session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    container.session_service.ensure_access(
        session,
        resolve_request_user_context(http_request, container),
    )
    return SessionStateResponse(
        session=session,
        state=container.session_service.resolve_state(session_id),
    )
