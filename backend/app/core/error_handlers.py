from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app.core.exceptions import AppError


logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "app error method=%s path=%s status=%s request_id=%s message=%s",
            request.method,
            request.url.path,
            exc.status_code,
            request_id,
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "detail": exc.message},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "unexpected error method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal server error", "detail": str(exc)},
        )
