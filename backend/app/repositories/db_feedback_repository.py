from __future__ import annotations

from backend.app.models.feedback import FeedbackRecord
from backend.app.repositories.db_repository_utils import as_datetime
from backend.app.services.database_connector import DatabaseConnector


class DbFeedbackRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def append(self, record: FeedbackRecord) -> FeedbackRecord:
        self.database_connector.execute_write(
            """
            INSERT INTO feedback_logs (
                feedback_id, session_id, trace_id, user_id, feedback_type, comment, created_at
            ) VALUES (
                :feedback_id, :session_id, :trace_id, :user_id, :feedback_type, :comment, :created_at
            )
            """,
            {
                "feedback_id": record.id,
                "session_id": record.session_id,
                "trace_id": record.trace_id,
                "user_id": record.user_id,
                "feedback_type": record.feedback_type,
                "comment": record.comment,
                "created_at": record.created_at,
            },
        )
        return record

    def list_records(
        self,
        session_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackRecord]:
        clauses: list[str] = []
        params: dict[str, object] = {"limit": limit}
        if session_id:
            clauses.append("session_id = :session_id")
            params["session_id"] = session_id
        if trace_id:
            clauses.append("trace_id = :trace_id")
            params["trace_id"] = trace_id
        if user_id:
            clauses.append("user_id = :user_id")
            params["user_id"] = user_id
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.database_connector.fetch_all(
            f"""
            SELECT feedback_id, session_id, trace_id, user_id, feedback_type, comment, created_at
            FROM feedback_logs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT :limit
            """,
            params,
        )
        return [
            FeedbackRecord(
                id=row["feedback_id"],
                session_id=row["session_id"],
                trace_id=row["trace_id"],
                user_id=row["user_id"],
                feedback_type=row["feedback_type"],
                comment=row["comment"],
                created_at=as_datetime(row["created_at"]),
            )
            for row in rows
        ]
