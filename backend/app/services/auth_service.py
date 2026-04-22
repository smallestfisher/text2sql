from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import uuid

from backend.app.core.exceptions import PermissionDeniedError
from backend.app.models.auth import (
    AuthUserRecord,
    BootstrapAdminRequest,
    DataScopeUpdateRequest,
    FieldVisibilityUpdateRequest,
    LoginRequest,
    LoginResponse,
    FieldVisibilityPolicy,
    PasswordChangeRequest,
    RoleRecord,
    RoleUpsertRequest,
    UserContext,
    UserUpsertRequest,
)


class AuthService:
    def __init__(
        self,
        repository,
        token_secret: str,
        token_ttl_seconds: int,
    ) -> None:
        self.repository = repository
        self.token_secret = token_secret.encode("utf-8")
        self.token_ttl_seconds = token_ttl_seconds

    def create_stub_user(
        self,
        user_id: str,
        username: str | None = None,
        roles: list[str] | None = None,
    ) -> UserContext:
        return UserContext(
            user_id=user_id,
            username=username or user_id,
            roles=roles or ["viewer"],
        )

    def has_users(self) -> bool:
        return self.repository.has_users()

    def bootstrap_admin(self, request: BootstrapAdminRequest) -> UserContext:
        if self.repository.has_users():
            raise PermissionDeniedError("bootstrap admin is only allowed before the first user is created")
        record = self._build_user_record(
            user_id=f"user_{uuid.uuid4().hex[:12]}",
            request=UserUpsertRequest(
                username=request.username,
                password=request.password,
                roles=["admin"],
                can_view_sql=True,
                can_execute_sql=True,
                is_active=True,
            ),
            existing=None,
        )
        self.repository.upsert(record)
        return self._to_user_context(record)

    def login(self, request: LoginRequest) -> LoginResponse:
        user = self.repository.get_by_username(request.username)
        if user is None or not user.is_active or not self._verify_password(request.password, user.password_hash):
            raise PermissionDeniedError("invalid username or password")
        return LoginResponse(
            access_token=self._issue_token(user),
            expires_in=self.token_ttl_seconds,
            user=self._to_user_context(user),
        )

    def resolve_token(self, token: str) -> UserContext:
        payload = self._decode_token(token)
        user_id = payload.get("sub")
        if not isinstance(user_id, str):
            raise PermissionDeniedError("invalid token subject")
        exp = payload.get("exp")
        if not isinstance(exp, int) or datetime.now(tz=timezone.utc).timestamp() >= exp:
            raise PermissionDeniedError("token has expired")
        user = self.repository.get_by_user_id(user_id)
        if user is None or not user.is_active:
            raise PermissionDeniedError("user is not available")
        return self._to_user_context(user)

    def list_users(self) -> list[UserContext]:
        return [self._to_user_context(item) for item in self.repository.list_users()]

    def get_user(self, user_id: str) -> UserContext | None:
        user = self.repository.get_by_user_id(user_id)
        return None if user is None else self._to_user_context(user)

    def list_roles(self) -> list[RoleRecord]:
        return self.repository.list_roles()

    def upsert_role(self, role_name: str, request: RoleUpsertRequest) -> RoleRecord:
        existing = {item.role_name: item for item in self.repository.list_roles()}
        created_at = (
            existing[role_name].created_at
            if role_name in existing
            else datetime.utcnow()
        )
        role = RoleRecord(
            role_name=role_name,
            description=request.description,
            created_at=created_at,
        )
        return self.repository.upsert_role(role)

    def upsert_user(self, user_id: str, request: UserUpsertRequest) -> UserContext:
        existing = self.repository.get_by_user_id(user_id)
        record = self._build_user_record(
            user_id=user_id,
            request=request,
            existing=existing,
        )
        self.repository.upsert(record)
        return self._to_user_context(record)

    def update_data_scope(self, user_id: str, request: DataScopeUpdateRequest) -> UserContext:
        existing = self.repository.get_by_user_id(user_id)
        if existing is None:
            raise KeyError(user_id)
        record = existing.model_copy(update={"data_scope": request.data_scope, "updated_at": datetime.utcnow()})
        self.repository.upsert(record)
        return self._to_user_context(record)

    def update_field_visibility(
        self,
        user_id: str,
        request: FieldVisibilityUpdateRequest,
    ) -> UserContext:
        existing = self.repository.get_by_user_id(user_id)
        if existing is None:
            raise KeyError(user_id)
        normalized = self._normalize_field_visibility(request.field_visibility)
        record = existing.model_copy(
            update={
                "field_visibility": normalized,
                "updated_at": datetime.utcnow(),
            }
        )
        self.repository.upsert(record)
        return self._to_user_context(record)

    def change_password(
        self,
        current_user: UserContext,
        request: PasswordChangeRequest,
    ) -> None:
        existing = self.repository.get_by_user_id(current_user.user_id)
        if existing is None or not existing.is_active:
            raise PermissionDeniedError("user is not available")
        if not self._verify_password(request.current_password, existing.password_hash):
            raise PermissionDeniedError("current password is incorrect")
        updated = existing.model_copy(
            update={
                "password_hash": self._hash_password(request.new_password),
                "updated_at": datetime.utcnow(),
            }
        )
        self.repository.upsert(updated)

    def _build_user_record(
        self,
        user_id: str,
        request: UserUpsertRequest,
        existing: AuthUserRecord | None,
    ) -> AuthUserRecord:
        password_hash = (
            self._hash_password(request.password)
            if request.password
            else (existing.password_hash if existing is not None else self._hash_password("change_me"))
        )
        created_at = existing.created_at if existing is not None else datetime.utcnow()
        return AuthUserRecord(
            user_id=user_id,
            username=request.username,
            password_hash=password_hash,
            roles=request.roles or (existing.roles if existing is not None else ["viewer"]),
            data_scope=request.data_scope,
            field_visibility=self._normalize_field_visibility(request.field_visibility),
            can_view_sql=request.can_view_sql,
            can_execute_sql=request.can_execute_sql,
            is_active=request.is_active,
            created_at=created_at,
            updated_at=datetime.utcnow(),
        )

    def _to_user_context(self, user: AuthUserRecord) -> UserContext:
        return UserContext(
            user_id=user.user_id,
            username=user.username,
            roles=user.roles,
            data_scope=user.data_scope,
            field_visibility=user.field_visibility,
            can_view_sql=user.can_view_sql,
            can_execute_sql=user.can_execute_sql,
        )

    def _normalize_field_visibility(
        self,
        policies: list[FieldVisibilityPolicy],
    ) -> list[FieldVisibilityPolicy]:
        deduplicated: dict[str, FieldVisibilityPolicy] = {}
        for item in policies:
            if not item.field_name.strip():
                continue
            deduplicated[item.field_name.strip()] = FieldVisibilityPolicy(
                field_name=item.field_name.strip(),
                mode=item.mode,
            )
        return [deduplicated[key] for key in sorted(deduplicated)]

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        )
        return f"{salt}${digest.hex()}"

    def _verify_password(self, password: str, encoded: str) -> bool:
        try:
            salt, digest_hex = encoded.split("$", 1)
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        )
        return hmac.compare_digest(digest.hex(), digest_hex)

    def _issue_token(self, user: AuthUserRecord) -> str:
        payload = {
            "sub": user.user_id,
            "username": user.username,
            "exp": int((datetime.now(tz=timezone.utc) + timedelta(seconds=self.token_ttl_seconds)).timestamp()),
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        payload_b64 = self._b64encode(payload_bytes)
        signature = hmac.new(self.token_secret, payload_b64.encode("utf-8"), hashlib.sha256).digest()
        return f"{payload_b64}.{self._b64encode(signature)}"

    def _decode_token(self, token: str) -> dict:
        try:
            payload_b64, signature_b64 = token.split(".", 1)
        except ValueError as exc:
            raise PermissionDeniedError("invalid token format") from exc
        expected_signature = hmac.new(
            self.token_secret,
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(self._b64decode(signature_b64), expected_signature):
            raise PermissionDeniedError("invalid token signature")
        return json.loads(self._b64decode(payload_b64).decode("utf-8"))

    def _b64encode(self, payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")

    def _b64decode(self, payload: str) -> bytes:
        padding = "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload + padding)
