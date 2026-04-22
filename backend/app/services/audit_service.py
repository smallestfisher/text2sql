from __future__ import annotations

import uuid

from backend.app.models.trace import TraceRecord, TraceStep


class AuditService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def new_trace(self) -> TraceRecord:
        return TraceRecord(trace_id=f"trace_{uuid.uuid4().hex[:12]}")

    def append_step(
        self,
        trace: TraceRecord,
        name: str,
        status: str,
        detail: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        trace.steps.append(
            TraceStep(
                name=name,
                status=status,
                detail=detail,
                metadata=metadata or {},
            )
        )

    def finalize(self, trace: TraceRecord, warnings: list[str] | None = None) -> TraceRecord:
        if warnings:
            trace.warnings.extend(warnings)
        return self.repository.append(trace)

    def get_trace(self, trace_id: str) -> TraceRecord | None:
        return self.repository.get_record(trace_id)
