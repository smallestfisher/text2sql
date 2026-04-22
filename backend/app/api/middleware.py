from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware


class RequestTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", f"req_{uuid.uuid4().hex[:12]}")
        request.state.request_id = request_id
        started_at = time.time()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = str(int((time.time() - started_at) * 1000))
        return response
