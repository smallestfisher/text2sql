from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import text

from backend.app.models.admin import SessionSnapshotRecord
from backend.app.models.conversation import ChatMessage, ChatSession
from backend.app.models.session_state import SessionState
from backend.app.repositories.db_repository_utils import as_datetime, json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbSessionRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def create_session(self, session: ChatSession) -> ChatSession:
        self.database_connector.execute_write(
            """
            INSERT INTO chat_sessions (
                session_id, user_id, title, status, current_state_json, created_at, updated_at
            ) VALUES (
                :session_id, :user_id, :title, :status, :current_state_json, :created_at, :updated_at
            )
            """,
            {
                "session_id": session.id,
                "user_id": session.user_id,
                "title": session.title,
                "status": session.status,
                "current_state_json": json_dumps(session.last_state.model_dump(mode="json")) if session.last_state else None,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
        )
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        row = self.database_connector.fetch_one(
            """
            SELECT session_id, user_id, title, status, current_state_json, created_at, updated_at
            FROM chat_sessions
            WHERE session_id = :session_id
            """,
            {"session_id": session_id},
        )
        if row is None:
            return None
        state_payload = json_loads(row.get("current_state_json"), None)
        return ChatSession(
            id=row["session_id"],
            user_id=row["user_id"],
            title=row["title"],
            status=row["status"],
            created_at=as_datetime(row["created_at"]),
            updated_at=as_datetime(row["updated_at"]),
            last_state=SessionState(**state_payload) if state_payload else None,
        )

    def list_sessions_by_user(self, user_id: str | None, limit: int = 50) -> list[ChatSession]:
        if user_id is None:
            rows = self.database_connector.fetch_all(
                """
                SELECT session_id, user_id, title, status, current_state_json, created_at, updated_at
                FROM chat_sessions
                WHERE user_id IS NULL
                ORDER BY updated_at DESC, created_at DESC
                LIMIT :limit
                """,
                {"limit": limit},
            )
        else:
            rows = self.database_connector.fetch_all(
                """
                SELECT session_id, user_id, title, status, current_state_json, created_at, updated_at
                FROM chat_sessions
                WHERE user_id = :user_id
                ORDER BY updated_at DESC, created_at DESC
                LIMIT :limit
                """,
                {"user_id": user_id, "limit": limit},
            )
        sessions: list[ChatSession] = []
        for row in rows:
            state_payload = json_loads(row.get("current_state_json"), None)
            sessions.append(
                ChatSession(
                    id=row["session_id"],
                    user_id=row["user_id"],
                    title=row["title"],
                    status=row["status"],
                    created_at=as_datetime(row["created_at"]),
                    updated_at=as_datetime(row["updated_at"]),
                    last_state=SessionState(**state_payload) if state_payload else None,
                )
            )
        return sessions

    def list_sessions(self, limit: int = 50) -> list[ChatSession]:
        rows = self.database_connector.fetch_all(
            """
            SELECT session_id, user_id, title, status, current_state_json, created_at, updated_at
            FROM chat_sessions
            ORDER BY updated_at DESC, created_at DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        sessions: list[ChatSession] = []
        for row in rows:
            state_payload = json_loads(row.get("current_state_json"), None)
            sessions.append(
                ChatSession(
                    id=row["session_id"],
                    user_id=row["user_id"],
                    title=row["title"],
                    status=row["status"],
                    created_at=as_datetime(row["created_at"]),
                    updated_at=as_datetime(row["updated_at"]),
                    last_state=SessionState(**state_payload) if state_payload else None,
                )
            )
        return sessions

    def list_messages(self, session_id: str) -> list[ChatMessage]:
        rows = self.database_connector.fetch_all(
            """
            SELECT message_id, session_id, role, content, trace_id, created_at
            FROM chat_messages
            WHERE session_id = :session_id
            ORDER BY created_at, message_id
            """,
            {"session_id": session_id},
        )
        return [
            ChatMessage(
                id=row["message_id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                trace_id=row["trace_id"],
                created_at=as_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def get_last_message(self, session_id: str) -> ChatMessage | None:
        row = self.database_connector.fetch_one(
            """
            SELECT message_id, session_id, role, content, trace_id, created_at
            FROM chat_messages
            WHERE session_id = :session_id
            ORDER BY created_at DESC, message_id DESC
            LIMIT 1
            """,
            {"session_id": session_id},
        )
        if row is None:
            return None
        return ChatMessage(
            id=row["message_id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            trace_id=row["trace_id"],
            created_at=as_datetime(row["created_at"]),
        )

    def list_state_snapshots(self, session_id: str, limit: int = 50) -> list[SessionSnapshotRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT snapshot_id, session_id, trace_id, state_json, created_at
            FROM session_state_snapshots
            WHERE session_id = :session_id
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT :limit
            """,
            {"session_id": session_id, "limit": limit},
        )
        snapshots: list[SessionSnapshotRecord] = []
        for row in rows:
            state_payload = json_loads(row["state_json"], {})
            snapshots.append(
                SessionSnapshotRecord(
                    snapshot_id=row["snapshot_id"],
                    session_id=row["session_id"],
                    trace_id=row["trace_id"],
                    state=SessionState(**state_payload),
                    created_at=as_datetime(row["created_at"]),
                )
            )
        return snapshots

    def append_message(self, message: ChatMessage) -> ChatMessage:
        with self.database_connector.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO chat_messages (message_id, session_id, role, content, trace_id, created_at)
                    VALUES (:message_id, :session_id, :role, :content, :trace_id, :created_at)
                    """
                ),
                {
                    "message_id": message.id,
                    "session_id": message.session_id,
                    "role": message.role,
                    "content": message.content,
                    "trace_id": message.trace_id,
                    "created_at": message.created_at,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE chat_sessions
                    SET updated_at = :updated_at
                    WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": message.session_id,
                    "updated_at": datetime.utcnow(),
                },
            )
        return message

    def ensure_title(self, session_id: str, title: str) -> None:
        self.database_connector.execute_write(
            """
            UPDATE chat_sessions
            SET title = :title, updated_at = :updated_at
            WHERE session_id = :session_id AND (title IS NULL OR title = '')
            """,
            {
                "session_id": session_id,
                "title": title,
                "updated_at": datetime.utcnow(),
            },
        )

    def update_state(self, session_id: str, session_state: SessionState, trace_id: str | None = None) -> None:
        snapshot_id = f"ss_{uuid.uuid4().hex[:16]}"
        state_json = json_dumps(session_state.model_dump(mode="json"))
        updated_at = datetime.utcnow()
        with self.database_connector.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE chat_sessions
                    SET current_state_json = :current_state_json, updated_at = :updated_at
                    WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": session_id,
                    "current_state_json": state_json,
                    "updated_at": updated_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO session_state_snapshots (snapshot_id, session_id, trace_id, state_json, created_at)
                    VALUES (:snapshot_id, :session_id, :trace_id, :state_json, :created_at)
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "state_json": state_json,
                    "created_at": updated_at,
                },
            )

    def update_status(self, session_id: str, status: str) -> None:
        self.database_connector.execute_write(
            """
            UPDATE chat_sessions
            SET status = :status, updated_at = :updated_at
            WHERE session_id = :session_id
            """,
            {
                "session_id": session_id,
                "status": status,
                "updated_at": datetime.utcnow(),
            },
        )

    def delete_session(self, session_id: str) -> bool:
        with self.database_connector.begin() as connection:
            existing = connection.execute(
                text("SELECT session_id FROM chat_sessions WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).mappings().first()
            if existing is None:
                return False
            connection.execute(
                text("DELETE FROM session_state_snapshots WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
            connection.execute(
                text("DELETE FROM chat_messages WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
            connection.execute(
                text("DELETE FROM chat_sessions WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
        return True
