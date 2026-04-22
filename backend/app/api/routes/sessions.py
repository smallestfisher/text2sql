from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.api.dependencies import get_container, resolve_request_user_context
from backend.app.core.container import AppContainer
from backend.app.models.conversation import (
    SessionCollectionResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionHistoryResponse,
    SessionStatusUpdateRequest,
    SessionStateResponse,
)
from backend.app.models.admin import SessionSnapshotRecord


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


@router.get("/sessions", response_model=SessionCollectionResponse)
def list_sessions(
    http_request: Request,
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> SessionCollectionResponse:
    user_context = resolve_request_user_context(http_request, container)
    sessions = container.session_service.list_sessions(
        user_id=user_context.user_id if user_context else None,
        limit=limit,
    )
    return SessionCollectionResponse(sessions=sessions, count=len(sessions))


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


@router.put("/sessions/{session_id}/status", response_model=SessionCreateResponse)
def update_session_status(
    session_id: str,
    request: SessionStatusUpdateRequest,
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
    container.session_service.update_status(session_id, request.status)
    updated = container.session_service.get_session(session_id)
    return SessionCreateResponse(session=updated)


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> dict:
    session = container.session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    container.session_service.ensure_access(
        session,
        resolve_request_user_context(http_request, container),
    )
    container.session_service.delete_session(session_id)
    return {"deleted": True}


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


@router.get("/snapshots/{session_id}", response_model=list[SessionSnapshotRecord])
def list_session_snapshots(
    session_id: str,
    http_request: Request,
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> list[SessionSnapshotRecord]:
    session = container.session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    container.session_service.ensure_access(
        session,
        resolve_request_user_context(http_request, container),
    )
    return container.session_repository.list_state_snapshots(session_id=session_id, limit=limit)
