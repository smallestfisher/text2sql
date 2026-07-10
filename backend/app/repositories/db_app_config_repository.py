from __future__ import annotations

from datetime import datetime, timezone

from backend.app.services.database_connector import DatabaseConnector


class DbAppConfigRepository:
    """Stores editable runtime configuration overrides in the runtime MySQL
    ``app_config`` table. Each row is one env-named key with its raw string
    value; the effective Settings are built by layering these overrides on top
    of the environment baseline (see ``Settings.build``).
    """

    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def read_all(self) -> dict[str, str]:
        rows = self.database_connector.fetch_all(
            "SELECT name, value_text FROM app_config"
        )
        return {row["name"]: row.get("value_text") for row in rows}

    def upsert(self, name: str, value: str) -> None:
        params = {
            "name": name,
            "value_text": value,
            "updated_at": datetime.now(tz=timezone.utc),
        }
        updated = self.database_connector.execute_write(
            """
            UPDATE app_config
            SET value_text = :value_text,
                updated_at = :updated_at
            WHERE name = :name
            """,
            params,
        )
        if updated == 0:
            self.database_connector.execute_write(
                """
                INSERT INTO app_config (name, value_text, updated_at)
                VALUES (:name, :value_text, :updated_at)
                """,
                params,
            )

    def delete(self, name: str) -> None:
        self.database_connector.execute_write(
            "DELETE FROM app_config WHERE name = :name",
            {"name": name},
        )
