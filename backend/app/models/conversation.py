from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .auth import UserContext
from .session_state import SessionState


MessageRole = Literal["user", "assistant", "system"]


class ChatMessage(BaseModel):
    id: str
    session_id: str
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    trace_id: str | None = None


class ChatSession(BaseModel):
    id: str
    user_id: str | None = None
    title: str | None = None
    status: Literal["active", "archived"] = "active"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_state: SessionState | None = None


class SessionCreateRequest(BaseModel):
    title: str | None = None
    user_context: UserContext | None = None


class SessionCreateResponse(BaseModel):
    session: ChatSession


class SessionCollectionResponse(BaseModel):
    sessions: list[ChatSession]
    count: int


class SessionHistoryResponse(BaseModel):
    session: ChatSession
    messages: list[ChatMessage]


class SessionStateResponse(BaseModel):
    session: ChatSession
    state: SessionState | None = None


class SessionStatusUpdateRequest(BaseModel):
    status: Literal["active", "archived"]
