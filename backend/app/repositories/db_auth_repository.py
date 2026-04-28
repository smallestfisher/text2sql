from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from backend.app.models.auth import AuthUserRecord, RoleRecord
from backend.app.repositories.db_repository_utils import as_datetime
from backend.app.services.database_connector import DatabaseConnector


class DbAuthRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def list_users(self) -> list[AuthUserRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT user_id, username, password_hash, is_active, created_at, updated_at
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
            SELECT user_id, username, password_hash, is_active, created_at, updated_at
            FROM users
            WHERE user_id = :user_id
            """,
            {"user_id": user_id},
        )
        return None if row is None else self._hydrate_user(row)

    def get_by_username(self, username: str) -> AuthUserRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT user_id, username, password_hash, is_active, created_at, updated_at
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
                text("DELETE FROM users WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        user_id, username, password_hash, is_active, created_at, updated_at
                    ) VALUES (
                        :user_id, :username, :password_hash, :is_active, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "user_id": user.user_id,
                    "username": user.username,
                    "password_hash": user.password_hash,
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

        return user

    def delete_user(self, user_id: str) -> bool:
        with self.database_connector.begin() as connection:
            existing = connection.execute(
                text("SELECT user_id FROM users WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().first()
            if existing is None:
                return False
            connection.execute(
                text("DELETE FROM user_roles WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            connection.execute(
                text("DELETE FROM users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        return True

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
        return AuthUserRecord(
            user_id=row["user_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            roles=[item["role_name"] for item in role_rows],
            is_active=bool(row["is_active"]),
            created_at=as_datetime(row["created_at"]),
            updated_at=as_datetime(row["updated_at"]),
        )
