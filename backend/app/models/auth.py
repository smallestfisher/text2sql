from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal


FieldVisibilityMode = Literal["visible", "masked", "hidden"]


class DataScope(BaseModel):
    factories: list[str] = Field(default_factory=list)
    sbus: list[str] = Field(default_factory=list)
    bus: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)


class FieldVisibilityPolicy(BaseModel):
    field_name: str
    mode: FieldVisibilityMode = "visible"


class UserContext(BaseModel):
    user_id: str
    username: str | None = None
    roles: list[str] = Field(default_factory=list)
    data_scope: DataScope = Field(default_factory=DataScope)
    field_visibility: list[FieldVisibilityPolicy] = Field(default_factory=list)
    can_view_sql: bool = True
    can_execute_sql: bool = True
    can_download_results: bool = True
    is_active: bool = True


class AuthUserRecord(BaseModel):
    user_id: str
    username: str
    password_hash: str
    roles: list[str] = Field(default_factory=list)
    data_scope: DataScope = Field(default_factory=DataScope)
    field_visibility: list[FieldVisibilityPolicy] = Field(default_factory=list)
    can_view_sql: bool = True
    can_execute_sql: bool = True
    can_download_results: bool = True
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
    data_scope: DataScope = Field(default_factory=DataScope)
    field_visibility: list[FieldVisibilityPolicy] = Field(default_factory=list)
    can_view_sql: bool = True
    can_execute_sql: bool = True
    can_download_results: bool = True
    is_active: bool = True


class DataScopeUpdateRequest(BaseModel):
    data_scope: DataScope = Field(default_factory=DataScope)


class FieldVisibilityUpdateRequest(BaseModel):
    field_visibility: list[FieldVisibilityPolicy] = Field(default_factory=list)


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
