from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from functools import lru_cache

from backend.app.core.container import AppContainer
from backend.app.models.auth import UserContext


@lru_cache(maxsize=1)
def get_container() -> AppContainer:
    return AppContainer()


def reset_container() -> AppContainer:
    get_container.cache_clear()
    return get_container()


def resolve_request_user_context(
    request: Request,
    container: AppContainer,
    default_user_context: UserContext | None = None,
) -> UserContext | None:
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            return container.auth_service.resolve_token(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    return default_user_context


def get_current_user(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    user = resolve_request_user_context(request, container)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_admin_user(
    current_user: UserContext = Depends(get_current_user),
) -> UserContext:
    if "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="admin role required")
    return current_user
