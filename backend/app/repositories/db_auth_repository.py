from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import text

from backend.app.models.auth import AuthUserRecord, DataScope, FieldVisibilityPolicy, RoleRecord
from backend.app.repositories.db_repository_utils import as_datetime
from backend.app.services.database_connector import DatabaseConnector


class DbAuthRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def list_users(self) -> list[AuthUserRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT user_id, username, password_hash, can_view_sql, can_execute_sql, is_active, created_at, updated_at
            FROM users
            ORDER BY username
            """
        )
        return [self._hydrate_user(row) for row in rows]

    def list_roles(self) -> list[RoleRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT role_name, description, created_at
            FROM roles
            ORDER BY role_name
            """
        )
        return [
            RoleRecord(
                role_name=row["role_name"],
                description=row["description"],
                created_at=as_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def upsert_role(self, role: RoleRecord) -> RoleRecord:
        self.database_connector.execute_write(
            """
            INSERT INTO roles (role_name, description, created_at)
            VALUES (:role_name, :description, :created_at)
            ON DUPLICATE KEY UPDATE description = VALUES(description)
            """,
            {
                "role_name": role.role_name,
                "description": role.description,
                "created_at": role.created_at,
            },
        )
        return role

    def get_by_user_id(self, user_id: str) -> AuthUserRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT user_id, username, password_hash, can_view_sql, can_execute_sql, is_active, created_at, updated_at
            FROM users
            WHERE user_id = :user_id
            """,
            {"user_id": user_id},
        )
        return None if row is None else self._hydrate_user(row)

    def get_by_username(self, username: str) -> AuthUserRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT user_id, username, password_hash, can_view_sql, can_execute_sql, is_active, created_at, updated_at
            FROM users
            WHERE username = :username
            """,
            {"username": username.strip()},
        )
        return None if row is None else self._hydrate_user(row)

    def upsert(self, user: AuthUserRecord) -> AuthUserRecord:
        with self.database_connector.begin() as connection:
            connection.execute(
                text("DELETE FROM user_roles WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
            connection.execute(
                text("DELETE FROM data_permissions WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
            connection.execute(
                text("DELETE FROM field_visibility_policies WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
            connection.execute(
                text("DELETE FROM users WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        user_id, username, password_hash, can_view_sql, can_execute_sql,
                        is_active, created_at, updated_at
                    ) VALUES (
                        :user_id, :username, :password_hash, :can_view_sql, :can_execute_sql,
                        :is_active, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "user_id": user.user_id,
                    "username": user.username,
                    "password_hash": user.password_hash,
                    "can_view_sql": user.can_view_sql,
                    "can_execute_sql": user.can_execute_sql,
                    "is_active": user.is_active,
                    "created_at": user.created_at,
                    "updated_at": user.updated_at,
                },
            )

            existing_roles = {
                item["role_name"]
                for item in connection.execute(text("SELECT role_name FROM roles"))
                .mappings()
                .all()
            }
            now = datetime.utcnow()
            for role_name in user.roles:
                if role_name not in existing_roles:
                    connection.execute(
                        text(
                            """
                            INSERT INTO roles (role_name, description, created_at)
                            VALUES (:role_name, :description, :created_at)
                            """
                        ),
                        {
                            "role_name": role_name,
                            "description": None,
                            "created_at": now,
                        },
                    )
                    existing_roles.add(role_name)
                connection.execute(
                    text(
                        """
                        INSERT INTO user_roles (user_id, role_name, created_at)
                        VALUES (:user_id, :role_name, :created_at)
                        """
                    ),
                    {
                        "user_id": user.user_id,
                        "role_name": role_name,
                        "created_at": now,
                    },
                )

            for scope_type, scope_value in self._permission_rows(user.data_scope):
                connection.execute(
                    text(
                        """
                        INSERT INTO data_permissions (permission_id, user_id, scope_type, scope_value, created_at)
                        VALUES (:permission_id, :user_id, :scope_type, :scope_value, :created_at)
                        """
                    ),
                    {
                        "permission_id": f"perm_{uuid.uuid4().hex[:16]}",
                        "user_id": user.user_id,
                        "scope_type": scope_type,
                        "scope_value": scope_value,
                        "created_at": now,
                    },
                )

            for policy in user.field_visibility:
                connection.execute(
                    text(
                        """
                        INSERT INTO field_visibility_policies (
                            policy_id, user_id, field_name, visibility_mode, created_at
                        ) VALUES (
                            :policy_id, :user_id, :field_name, :visibility_mode, :created_at
                        )
                        """
                    ),
                    {
                        "policy_id": f"fvp_{uuid.uuid4().hex[:16]}",
                        "user_id": user.user_id,
                        "field_name": policy.field_name,
                        "visibility_mode": policy.mode,
                        "created_at": now,
                    },
                )

        return user

    def has_users(self) -> bool:
        row = self.database_connector.fetch_one("SELECT COUNT(*) AS total FROM users")
        return bool(row and row["total"])

    def _hydrate_user(self, row: dict) -> AuthUserRecord:
        user_id = row["user_id"]
        role_rows = self.database_connector.fetch_all(
            """
            SELECT role_name
            FROM user_roles
            WHERE user_id = :user_id
            ORDER BY role_name
            """,
            {"user_id": user_id},
        )
        permission_rows = self.database_connector.fetch_all(
            """
            SELECT scope_type, scope_value
            FROM data_permissions
            WHERE user_id = :user_id
            ORDER BY scope_type, scope_value
            """,
            {"user_id": user_id},
        )
        field_visibility_rows = self.database_connector.fetch_all(
            """
            SELECT field_name, visibility_mode
            FROM field_visibility_policies
            WHERE user_id = :user_id
            ORDER BY field_name
            """,
            {"user_id": user_id},
        )
        return AuthUserRecord(
            user_id=row["user_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            roles=[item["role_name"] for item in role_rows],
            data_scope=self._build_data_scope(permission_rows),
            field_visibility=self._build_field_visibility(field_visibility_rows),
            can_view_sql=bool(row["can_view_sql"]),
            can_execute_sql=bool(row["can_execute_sql"]),
            is_active=bool(row["is_active"]),
            created_at=as_datetime(row["created_at"]),
            updated_at=as_datetime(row["updated_at"]),
        )

    def _build_data_scope(self, rows: list[dict]) -> DataScope:
        values: dict[str, list[str]] = {
            "factories": [],
            "sbus": [],
            "bus": [],
            "customers": [],
            "products": [],
        }
        for item in rows:
            scope_type = item["scope_type"]
            if scope_type in values:
                values[scope_type].append(item["scope_value"])
        return DataScope(**values)

    def _permission_rows(self, data_scope: DataScope) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for scope_type in ("factories", "sbus", "bus", "customers", "products"):
            for value in getattr(data_scope, scope_type):
                rows.append((scope_type, value))
        return rows

    def _build_field_visibility(self, rows: list[dict]) -> list[FieldVisibilityPolicy]:
        return [
            FieldVisibilityPolicy(
                field_name=row["field_name"],
                mode=row["visibility_mode"],
            )
            for row in rows
        ]
