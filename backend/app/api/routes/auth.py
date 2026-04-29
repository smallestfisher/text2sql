from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.dependencies import get_container, get_current_user
from backend.app.core.container import AppContainer
from backend.app.models.auth import (
    BootstrapAdminRequest,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    UserContext,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/bootstrap-status")
def bootstrap_status(container: AppContainer = Depends(get_container)) -> dict:
    return {"has_users": container.auth_service.has_users()}


@router.post("/bootstrap-admin", response_model=UserContext)
def bootstrap_admin(
    request: BootstrapAdminRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    try:
        return container.auth_service.bootstrap_admin(request)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/login", response_model=LoginResponse)
def login(
    request: LoginRequest,
    container: AppContainer = Depends(get_container),
) -> LoginResponse:
    try:
        return container.auth_service.login(request)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me", response_model=UserContext)
def me(current_user: UserContext = Depends(get_current_user)) -> UserContext:
    return current_user


@router.post("/change-password")
def change_password(
    request: PasswordChangeRequest,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        container.auth_service.change_password(current_user, request)
        return {"updated": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
