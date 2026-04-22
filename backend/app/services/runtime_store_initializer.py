from __future__ import annotations

from backend.app.config import RUNTIME_STORE_SCHEMA_PATH
from backend.app.services.database_connector import DatabaseConnector


class RuntimeStoreInitializer:
    def __init__(
        self,
        database_connector: DatabaseConnector,
        schema_path=RUNTIME_STORE_SCHEMA_PATH,
    ) -> None:
        self.database_connector = database_connector
        self.schema_path = schema_path

    def ensure_schema(self) -> dict:
        if not self.database_connector.connected:
            return {"executed": False, "error": "database connector is not configured"}
        database_result = self.database_connector.ensure_database_exists()
        if not database_result.get("executed"):
            return database_result
        sql_script = self.schema_path.read_text(encoding="utf-8")
        schema_result = self.database_connector.execute_script(sql_script)
        if schema_result.get("executed"):
            schema_result["database"] = database_result.get("database")
        return schema_result
