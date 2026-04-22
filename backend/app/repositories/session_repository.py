from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.app.config import SESSIONS_DATA_PATH
from backend.app.models.conversation import ChatMessage, ChatSession
from backend.app.models.session_state import SessionState


class InMemorySessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, ChatSession] = {}
        self.messages: dict[str, list[ChatMessage]] = {}

    def create_session(self, session: ChatSession) -> ChatSession:
        self.sessions[session.id] = session
        self.messages.setdefault(session.id, [])
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self.sessions.get(session_id)

    def list_messages(self, session_id: str) -> list[ChatMessage]:
        return list(self.messages.get(session_id, []))

    def append_message(self, message: ChatMessage) -> ChatMessage:
        self.messages.setdefault(message.session_id, []).append(message)
        session = self.sessions.get(message.session_id)
        if session is not None:
            session.updated_at = datetime.utcnow()
        return message

    def update_state(self, session_id: str, session_state: SessionState) -> None:
        session = self.sessions.get(session_id)
        if session is not None:
            session.last_state = session_state
            session.updated_at = datetime.utcnow()


class FileSessionRepository(InMemorySessionRepository):
    def __init__(self, path: Path = SESSIONS_DATA_PATH) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def create_session(self, session: ChatSession) -> ChatSession:
        created = super().create_session(session)
        self._save()
        return created

    def append_message(self, message: ChatMessage) -> ChatMessage:
        appended = super().append_message(message)
        self._save()
        return appended

    def update_state(self, session_id: str, session_state: SessionState) -> None:
        super().update_state(session_id, session_state)
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.sessions = {
            item["id"]: ChatSession(**item)
            for item in payload.get("sessions", [])
        }
        self.messages = {
            session_id: [ChatMessage(**message) for message in messages]
            for session_id, messages in payload.get("messages", {}).items()
        }

    def _save(self) -> None:
        payload = {
            "sessions": [session.model_dump(mode="json") for session in self.sessions.values()],
            "messages": {
                session_id: [message.model_dump(mode="json") for message in messages]
                for session_id, messages in self.messages.items()
            },
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
