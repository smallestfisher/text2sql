from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from backend.app.core.exceptions import PermissionDeniedError
from backend.app.models.auth import UserContext
from backend.app.models.conversation import ChatMessage, ChatSession
from backend.app.models.session_state import SessionState


class SessionService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def create_session(self, user_id: str | None = None, title: str | None = None) -> ChatSession:
        session = ChatSession(
            id=f"sess_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            title=title,
        )
        return self.repository.create_session(session)

    def get_session(self, session_id: str) -> ChatSession | None:
        return self.repository.get_session(session_id)

    def list_sessions(self, user_id: str | None = None, limit: int = 50) -> list[ChatSession]:
        return self.repository.list_sessions_by_user(user_id=user_id, limit=limit)

    def history(self, session_id: str) -> list[ChatMessage]:
        return self.repository.list_messages(session_id)

    def _next_message_created_at(self, session_id: str) -> datetime:
        next_created_at = datetime.now(timezone.utc)
        last_message = self.repository.get_last_message(session_id)
        if last_message is not None:
            candidate = last_message.created_at + timedelta(seconds=1)
            if candidate > next_created_at:
                next_created_at = candidate
        return next_created_at

    def append_user_message(self, session_id: str, content: str, trace_id: str | None = None) -> ChatMessage:
        session = self.repository.get_session(session_id)
        if session is not None and not session.title:
            self.repository.ensure_title(session_id, content[:40])
        created_at = self._next_message_created_at(session_id)
        return self.repository.append_message(
            ChatMessage(
                id=f"msg_{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                role="user",
                content=content,
                trace_id=trace_id,
                created_at=created_at,
            )
        )

    def append_assistant_message(self, session_id: str, content: str, trace_id: str | None = None) -> ChatMessage:
        created_at = self._next_message_created_at(session_id)
        return self.repository.append_message(
            ChatMessage(
                id=f"msg_{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                role="assistant",
                content=content,
                trace_id=trace_id,
                created_at=created_at,
            )
        )

    def resolve_state(self, session_id: str) -> SessionState | None:
        session = self.repository.get_session(session_id)
        if session is None:
            return None
        return session.last_state

    def update_state(self, session_id: str, session_state: SessionState, trace_id: str | None = None) -> None:
        self.repository.update_state(session_id, session_state, trace_id=trace_id)

    def update_status(self, session_id: str, status: str) -> None:
        self.repository.update_status(session_id, status)

    def delete_session(self, session_id: str) -> None:
        deleted = self.repository.delete_session(session_id)
        if not deleted:
            raise KeyError(session_id)

    def ensure_access(self, session: ChatSession, user_context: UserContext | None) -> None:
        if user_context is None or session.user_id is None:
            return
        if session.user_id != user_context.user_id and "admin" not in user_context.roles:
            raise PermissionDeniedError("current user cannot access this session")
