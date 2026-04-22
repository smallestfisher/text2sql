from __future__ import annotations

from backend.app.models.trace import TraceRecord
from backend.app.repositories.db_repository_utils import as_datetime, json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbAuditRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def append(self, record: TraceRecord) -> TraceRecord:
        self.database_connector.execute_write(
            """
            DELETE FROM query_logs
            WHERE trace_id = :trace_id
            """,
            {"trace_id": record.trace_id},
        )
        self.database_connector.execute_write(
            """
            INSERT INTO query_logs (trace_id, trace_json, created_at)
            VALUES (:trace_id, :trace_json, :created_at)
            """,
            {
                "trace_id": record.trace_id,
                "trace_json": json_dumps(record.model_dump(mode="json")),
                "created_at": record.created_at,
            },
        )
        return record

    def list_records(self) -> list[TraceRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT trace_json
            FROM query_logs
            ORDER BY created_at DESC
            """
        )
        return [TraceRecord(**json_loads(row["trace_json"], {})) for row in rows]

    def get_record(self, trace_id: str) -> TraceRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT trace_json
            FROM query_logs
            WHERE trace_id = :trace_id
            """,
            {"trace_id": trace_id},
        )
        if row is None:
            return None
        payload = json_loads(row["trace_json"], {})
        if payload.get("created_at") is not None:
            payload["created_at"] = as_datetime(payload["created_at"])
        return TraceRecord(**payload)
