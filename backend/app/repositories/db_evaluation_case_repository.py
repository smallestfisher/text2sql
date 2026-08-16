from __future__ import annotations

from datetime import datetime, timezone

from backend.app.models.evaluation import EvaluationCase
from backend.app.repositories.db_repository_utils import json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbEvaluationCaseRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def list_cases(self) -> list[EvaluationCase]:
        rows = self.database_connector.fetch_all(
            """
            SELECT case_json
            FROM evaluation_cases
            ORDER BY created_at, case_id
            """
        )
        return [EvaluationCase(**json_loads(row["case_json"], {})) for row in rows]

    def get(self, case_id: str) -> EvaluationCase | None:
        row = self.database_connector.fetch_one(
            """
            SELECT case_json
            FROM evaluation_cases
            WHERE case_id = :case_id
            """,
            {"case_id": case_id},
        )
        if row is None:
            return None
        return EvaluationCase(**json_loads(row["case_json"], {}))

    def create(self, case: EvaluationCase) -> EvaluationCase:
        now = datetime.now(tz=timezone.utc)
        self.database_connector.execute_write(
            """
            INSERT INTO evaluation_cases (case_id, case_json, created_at, updated_at)
            VALUES (:case_id, :case_json, :created_at, :updated_at)
            """,
            {
                "case_id": case.id,
                "case_json": json_dumps(case.model_dump(mode="json")),
                "created_at": now,
                "updated_at": now,
            },
        )
        return case
