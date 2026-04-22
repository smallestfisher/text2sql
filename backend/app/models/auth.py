from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class DataScope(BaseModel):
    factories: list[str] = Field(default_factory=list)
    sbus: list[str] = Field(default_factory=list)
    bus: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)


class UserContext(BaseModel):
    user_id: str
    username: str | None = None
    roles: list[str] = Field(default_factory=list)
    data_scope: DataScope = Field(default_factory=DataScope)
    can_view_sql: bool = True
    can_execute_sql: bool = True


class AuthUserRecord(BaseModel):
    user_id: str
    username: str
    password_hash: str
    roles: list[str] = Field(default_factory=list)
    data_scope: DataScope = Field(default_factory=DataScope)
    can_view_sql: bool = True
    can_execute_sql: bool = True
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BootstrapAdminRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class UserUpsertRequest(BaseModel):
    username: str
    password: str | None = None
    roles: list[str] = Field(default_factory=list)
    data_scope: DataScope = Field(default_factory=DataScope)
    can_view_sql: bool = True
    can_execute_sql: bool = True
    is_active: bool = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserContext
