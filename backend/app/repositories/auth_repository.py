from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import AUTH_USERS_DATA_PATH
from backend.app.models.auth import AuthUserRecord


class FileAuthRepository:
    def __init__(self, path: Path = AUTH_USERS_DATA_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.users: dict[str, AuthUserRecord] = {}
        self._load()

    def list_users(self) -> list[AuthUserRecord]:
        return sorted(self.users.values(), key=lambda item: item.username)

    def get_by_user_id(self, user_id: str) -> AuthUserRecord | None:
        return self.users.get(user_id)

    def get_by_username(self, username: str) -> AuthUserRecord | None:
        lowered = username.strip().lower()
        for user in self.users.values():
            if user.username.lower() == lowered:
                return user
        return None

    def upsert(self, user: AuthUserRecord) -> AuthUserRecord:
        self.users[user.user_id] = user
        self._save()
        return user

    def has_users(self) -> bool:
        return bool(self.users)

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.users = {
            item["user_id"]: AuthUserRecord(**item)
            for item in payload
        }

    def _save(self) -> None:
        payload = [item.model_dump(mode="json") for item in self.list_users()]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
