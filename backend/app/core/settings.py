from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_env_file() -> None:
    candidates = [REPO_ROOT / ".env", REPO_ROOT / "env"]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        break


load_env_file()


class Settings(BaseModel):
    app_name: str = os.getenv("APP_NAME", "Text2SQL Backend")
    app_version: str = os.getenv("APP_VERSION", "0.3.0")
    app_env: str = os.getenv("APP_ENV", "dev")
    enable_docs: bool = os.getenv("ENABLE_DOCS", "true").lower() == "true"
    database_url: str | None = os.getenv("DATABASE_URL") or os.getenv("DB_URI")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_api_base: str | None = os.getenv("OPENAI_API_BASE")
    openai_model: str = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL", "stub")
    sql_timeout_seconds: int = int(os.getenv("SQL_TIMEOUT_SECONDS", "30"))
    default_sql_limit: int = int(os.getenv("DEFAULT_SQL_LIMIT", "200"))
    runtime_storage_mode: str = os.getenv("RUNTIME_STORAGE_MODE", "file")
    auth_token_secret: str = os.getenv("AUTH_TOKEN_SECRET", "dev-token-secret-change-me")
    auth_token_ttl_seconds: int = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "28800"))


settings = Settings()
