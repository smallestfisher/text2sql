from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from backend.app.core.container import AppContainer
from backend.app.models.auth import UserContext


logger = logging.getLogger(__name__)

# Explicit singleton holder (not lru_cache) so reset_container can rebuild
# atomically: a failed rebuild keeps the last working container in place instead
# of leaving an empty cache that re-fails on every subsequent request.
_container: AppContainer | None = None


def get_container() -> AppContainer:
    global _container
    if _container is None:
        _container = AppContainer()
    return _container


def reset_container() -> AppContainer:
    """Rebuild the container from current settings/overrides and swap it in.

    If construction raises, the previous container is kept intact and the error
    propagates to the caller — the app never ends up with no working container.
    On success the old container's connection pools are disposed.
    """
    global _container
    old = _container
    new = AppContainer()  # may raise; old remains installed if it does
    _container = new
    if old is not None and old is not new:
        try:
            old.dispose()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.warning("failed to dispose previous container", exc_info=True)
    return new


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
