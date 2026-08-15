from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.app.models.data_source import DataSourceCreateRequest, DataSourceRecord
from backend.app.repositories.db_repository_utils import as_datetime, json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbDataSourceRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def list(
        self,
        workspace_id: str | None = None,
        domain_id: str | None = None,
    ) -> list:
        conditions: list[str] = []
        params: dict[str, object] = {}
        if workspace_id:
            conditions.append("workspace_id = :workspace_id")
            params["workspace_id"] = workspace_id
        if domain_id:
            conditions.append("domain_id = :domain_id")
            params["domain_id"] = domain_id
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.database_connector.fetch_all(
            f"SELECT * FROM data_sources{where} ORDER BY updated_at DESC", params
        )
        return [self._record(row) for row in rows]

    def get(self, data_source_id: str) -> DataSourceRecord | None:
        row = self.database_connector.fetch_one(
            "SELECT * FROM data_sources WHERE id = :id", {"id": data_source_id}
        )
        return self._record(row) if row else None

    def get_database_url(self, data_source_id: str) -> str | None:
        row = self.database_connector.fetch_one(
            "SELECT database_url FROM data_sources WHERE id = :id", {"id": data_source_id}
        )
        return str(row["database_url"]) if row else None

    def create(self, payload: DataSourceCreateRequest) -> DataSourceRecord:
        now = datetime.now(tz=timezone.utc)
        data_source_id = uuid4().hex
        if not payload.database_url.lower().startswith(("oracle:", "oracle+")):
            raise ValueError("only Oracle data sources are supported")
        self.database_connector.execute_write(
            """
            INSERT INTO data_sources (
                id, workspace_id, domain_id, name, database_url, dialect, schemas_json,
                description, status, enabled, last_sync_at, last_error, created_at, updated_at
            ) VALUES (
                :id, :workspace_id, :domain_id, :name, :database_url, :dialect, :schemas_json,
                :description, 'draft', TRUE, NULL, NULL, :created_at, :updated_at
            )
            """,
            {
                "id": data_source_id,
                "workspace_id": payload.workspace_id,
                "domain_id": payload.domain_id,
                "name": payload.name,
                "database_url": payload.database_url,
                "dialect": "oracle",
                "schemas_json": json_dumps(payload.schemas),
                "description": payload.description,
                "created_at": now,
                "updated_at": now,
            },
        )
        record = self.get(data_source_id)
        if record is None:
            raise RuntimeError("failed to persist data source")
        return record

    def update_sync_result(
        self, data_source_id: str, *, status: str, error: str | None = None
    ) -> DataSourceRecord:
        now = datetime.now(tz=timezone.utc)
        self.database_connector.execute_write(
            """
            UPDATE data_sources
            SET status = :status, last_error = :error,
                last_sync_at = :last_sync_at, updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": data_source_id,
                "status": status,
                "error": error,
                "last_sync_at": now if status == "ready" else None,
                "updated_at": now,
            },
        )
        record = self.get(data_source_id)
        if record is None:
            raise KeyError(data_source_id)
        return record

    @staticmethod
    def _record(row: dict) -> DataSourceRecord:
        return DataSourceRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            domain_id=str(row["domain_id"]),
            name=str(row["name"]),
            dialect=str(row["dialect"]),
            schemas=json_loads(row.get("schemas_json"), []),
            description=row.get("description"),
            status=str(row.get("status") or "draft"),
            enabled=bool(row.get("enabled", True)),
            last_sync_at=as_datetime(row["last_sync_at"]) if row.get("last_sync_at") else None,
            last_error=row.get("last_error"),
            created_at=as_datetime(row["created_at"]),
            updated_at=as_datetime(row["updated_at"]),
        )
