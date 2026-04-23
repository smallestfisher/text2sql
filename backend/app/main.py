from __future__ import annotations

from fastapi import FastAPI

from backend.app.api.routes.admin import router as admin_router
from backend.app.api.middleware import RequestTraceMiddleware
from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.chat import router as chat_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.query import router as query_router
from backend.app.api.routes.semantic import router as semantic_router
from backend.app.api.routes.sessions import router as sessions_router
from backend.app.core.error_handlers import register_error_handlers
from backend.app.logging_config import configure_logging
from backend.app.core.settings import settings


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.add_middleware(RequestTraceMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(semantic_router)
    app.include_router(query_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    return app


app = create_app()
