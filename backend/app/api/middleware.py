from __future__ import annotations

import logging
import time
import uuid

from backend.app.logging_config import clear_request_id, clear_trace_id, set_request_id
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger(__name__)


class RequestTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", f"req_{uuid.uuid4().hex[:12]}")
        request.state.request_id = request_id
        set_request_id(request_id)
        clear_trace_id()
        started_at = time.time()
        logger.info(
            "request started method=%s path=%s query=%s request_id=%s",
            request.method,
            request.url.path,
            request.url.query or "-",
            request_id,
        )
        try:
            response = await call_next(request)
            elapsed_ms = int((time.time() - started_at) * 1000)
            logger.info(
                "request completed method=%s path=%s status=%s elapsed_ms=%s request_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
            return response
        finally:
            clear_trace_id()
            clear_request_id()
