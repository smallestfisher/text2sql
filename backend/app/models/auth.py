from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

ADMIN_ROLE = "admin"
VIEWER_ROLE = "viewer"
CHITCHAT_ROLE = "chitchat"


class UserContext(BaseModel):
    user_id: str
    username: str | None = None
    roles: list[str] = Field(default_factory=list)
    is_active: bool = True


class AuthUserRecord(BaseModel):
    user_id: str
    username: str
    password_hash: str
    roles: list[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BootstrapAdminRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class AdminPasswordResetRequest(BaseModel):
    new_password: str


class UserUpsertRequest(BaseModel):
    username: str
    password: str | None = None
    roles: list[str] = Field(default_factory=list)
    is_active: bool = True


class RoleRecord(BaseModel):
    role_name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RoleUpsertRequest(BaseModel):
    description: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserContext


def normalize_role_names(roles: list[str] | None) -> list[str]:
    if not roles:
        return []
    normalized: list[str] = []
    for role_name in roles:
        cleaned = role_name.strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def has_role(user_context: UserContext | None, role_name: str) -> bool:
    if user_context is None:
        return False
    return role_name in normalize_role_names(user_context.roles)
