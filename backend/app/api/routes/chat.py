from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from backend.app.api.dependencies import get_container, get_current_user, resolve_request_user_context
from backend.app.core.container import AppContainer
from backend.app.models.admin import (
    RuntimeQueryLogCollectionResponse,
    RuntimeRetrievalLogRecord,
    RuntimeSqlAuditRecord,
)
from backend.app.models.api import ChatResponse, PlanRequest
from backend.app.models.auth import UserContext
from backend.app.models.feedback import (
    FeedbackCollectionResponse,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackSummary,
)
from backend.app.models.trace import TraceRecord


router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/query/stream")
async def chat_query_stream(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    trace_id = container.audit_service.new_trace().trace_id
    queue = container.progress_service.subscribe(trace_id)

    async def event_stream():
        future = asyncio.create_task(asyncio.to_thread(container.orchestrator.chat, request, trace_id))
        try:
            while True:
                item = await asyncio.to_thread(queue.get)
                if item is None:
                    break
                payload = item.model_dump(mode="json")
                yield f"event: {item.type}\n".encode("utf-8")
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        finally:
            container.progress_service.unsubscribe(trace_id, queue)
            try:
                await future
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/query", response_model=ChatResponse)
def chat_query(
    request: PlanRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> ChatResponse:
    request.user_context = resolve_request_user_context(
        http_request,
        container,
        fallback=request.user_context,
    )
    return container.orchestrator.chat(request)


@router.post("/feedback", response_model=FeedbackRecord)
def submit_feedback(
    request: FeedbackRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> FeedbackRecord:
    user_context = resolve_request_user_context(http_request, container)
    if user_context is not None and not request.user_id:
        request.user_id = user_context.user_id
    return container.feedback_service.submit(request)


@router.get("/feedbacks", response_model=FeedbackCollectionResponse)
def list_my_feedbacks(
    session_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 100,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> FeedbackCollectionResponse:
    return container.feedback_service.list_records(
        session_id=session_id,
        trace_id=trace_id,
        user_id=current_user.user_id,
        limit=limit,
    )


@router.get("/feedbacks/summary", response_model=FeedbackSummary)
def summarize_my_feedbacks(
    session_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 100,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> FeedbackSummary:
    return container.feedback_service.summarize(
        session_id=session_id,
        trace_id=trace_id,
        user_id=current_user.user_id,
        limit=limit,
    )


@router.get("/query-logs", response_model=RuntimeQueryLogCollectionResponse)
def list_my_query_logs(
    session_id: str | None = None,
    limit: int = 50,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> RuntimeQueryLogCollectionResponse:
    query_logs = container.runtime_log_repository.list_query_logs(
        limit=limit,
        session_id=session_id,
        user_id=current_user.user_id,
    )
    return RuntimeQueryLogCollectionResponse(query_logs=query_logs, count=len(query_logs))


@router.get("/traces/{trace_id}", response_model=TraceRecord)
def get_my_trace(
    trace_id: str,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> TraceRecord:
    _ensure_trace_access(trace_id, current_user, container)
    trace = container.audit_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace


@router.get("/traces/{trace_id}/retrieval", response_model=list[RuntimeRetrievalLogRecord])
def get_my_trace_retrieval(
    trace_id: str,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> list[RuntimeRetrievalLogRecord]:
    _ensure_trace_access(trace_id, current_user, container)
    return container.runtime_log_repository.list_retrieval_logs(trace_id)


@router.get("/traces/{trace_id}/sql-audit", response_model=RuntimeSqlAuditRecord)
def get_my_trace_sql_audit(
    trace_id: str,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> RuntimeSqlAuditRecord:
    _ensure_trace_access(trace_id, current_user, container)
    record = container.runtime_log_repository.get_sql_audit(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="sql audit not found")
    return record


@router.get("/traces/{trace_id}/export")
def export_my_trace_result(
    trace_id: str,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> PlainTextResponse:
    _ensure_trace_access(trace_id, current_user, container)
    record = container.runtime_log_repository.get_sql_audit(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="sql audit not found")
    trace = container.audit_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    query_log = container.runtime_log_repository.get_query_log(trace_id)
    if query_log is None or not record.sql_text:
        raise HTTPException(status_code=404, detail="query result export is not available")
    execution = container.sql_executor.execute(record.sql_text, user_context=current_user)
    if execution is None or not execution.executed:
        raise HTTPException(status_code=400, detail="query result export is not available")
    columns = execution.columns
    rows = execution.rows
    lines = [",".join(_csv_escape(column) for column in columns)]
    for row in rows:
        lines.append(",".join(_csv_escape(row.get(column)) for column in columns))
    filename = f"trace_{trace_id}.csv"
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ensure_trace_access(
    trace_id: str,
    current_user: UserContext,
    container: AppContainer,
) -> None:
    query_log = container.runtime_log_repository.get_query_log(trace_id)
    if query_log is None:
        raise HTTPException(status_code=404, detail="trace not found")
    if query_log.session_id:
        session = container.session_service.get_session(query_log.session_id)
        if session is not None:
            container.session_service.ensure_access(session, current_user)
            return
    if query_log.user_id and query_log.user_id != current_user.user_id and "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="current user cannot access this trace")


def _csv_escape(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace('"', '""')
    return f'"{text}"'
