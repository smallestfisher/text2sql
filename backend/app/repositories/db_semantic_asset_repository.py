from __future__ import annotations

from datetime import datetime, timezone

from backend.app.repositories.db_repository_utils import json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbSemanticAssetRepository:
    """Stores semantic assets (tables_metadata, business_knowledge, join_patterns,
    examples_template) as whole documents in the runtime MySQL ``semantic_assets``
    table. Each asset is one row keyed by ``name`` with its content serialized to
    ``content_json``.
    """

    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def read_all(self) -> dict[str, object]:
        rows = self.database_connector.fetch_all(
            "SELECT name, content_json FROM semantic_assets"
        )
        return {row["name"]: json_loads(row.get("content_json"), None) for row in rows}

    def get(self, name: str) -> object | None:
        row = self.database_connector.fetch_one(
            "SELECT content_json FROM semantic_assets WHERE name = :name",
            {"name": name},
        )
        if row is None:
            return None
        return json_loads(row.get("content_json"), None)

    def has_any(self) -> bool:
        row = self.database_connector.fetch_one(
            "SELECT 1 AS present FROM semantic_assets LIMIT 1"
        )
        return row is not None

    def upsert(self, name: str, content, version: str | None = None) -> None:
        params = {
            "name": name,
            "content_json": json_dumps(content),
            "version": version,
            "updated_at": datetime.now(tz=timezone.utc),
        }
        updated = self.database_connector.execute_write(
            """
            UPDATE semantic_assets
            SET content_json = :content_json,
                version = :version,
                updated_at = :updated_at
            WHERE name = :name
            """,
            params,
        )
        if updated == 0:
            self.database_connector.execute_write(
                """
                INSERT INTO semantic_assets (name, content_json, version, updated_at)
                VALUES (:name, :content_json, :version, :updated_at)
                """,
                params,
            )
