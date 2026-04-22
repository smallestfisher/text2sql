from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.dependencies import get_container, get_current_user
from backend.app.core.container import AppContainer
from backend.app.models.auth import (
    BootstrapAdminRequest,
    LoginRequest,
    LoginResponse,
    UserContext,
)


class StubLoginRequest(BaseModel):
    user_id: str
    username: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["viewer"])


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


@router.post("/stub-login", response_model=UserContext)
def stub_login(
    request: StubLoginRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    return container.auth_service.create_stub_user(
        user_id=request.user_id,
        username=request.username,
        roles=request.roles,
    )
