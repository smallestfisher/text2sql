from __future__ import annotations

from backend.app.models.evaluation import EvaluationRunRecord
from backend.app.repositories.db_repository_utils import as_datetime, json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbEvaluationRunRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def append(self, record: EvaluationRunRecord) -> EvaluationRunRecord:
        self.database_connector.execute_write(
            """
            DELETE FROM evaluation_runs
            WHERE run_id = :run_id
            """,
            {"run_id": record.run_id},
        )
        self.database_connector.execute_write(
            """
            INSERT INTO evaluation_runs (
                run_id, case_count, passed_count, failed_count, run_json, created_at
            ) VALUES (
                :run_id, :case_count, :passed_count, :failed_count, :run_json, :created_at
            )
            """,
            {
                "run_id": record.run_id,
                "case_count": record.case_count,
                "passed_count": record.passed_count,
                "failed_count": record.failed_count,
                "run_json": json_dumps(record.model_dump(mode="json")),
                "created_at": record.created_at,
            },
        )
        return record

    def list_runs(self, limit: int = 100) -> list[EvaluationRunRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT run_json
            FROM evaluation_runs
            ORDER BY created_at DESC, run_id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        records: list[EvaluationRunRecord] = []
        for row in rows:
            payload = json_loads(row["run_json"], {})
            if payload.get("created_at") is not None:
                payload["created_at"] = as_datetime(payload["created_at"])
            records.append(EvaluationRunRecord(**payload))
        return records
