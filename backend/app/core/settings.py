from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.engine import make_url


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


def _raw_business_database_url() -> str | None:
    return (
        os.getenv("BUSINESS_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("DB_URI")
    )


def _resolve_runtime_database_url() -> str | None:
    explicit_runtime_url = (
        os.getenv("RUNTIME_DATABASE_URL")
        or os.getenv("AUTH_DATABASE_URL")
        or os.getenv("RUNTIME_DB_URI")
    )
    if explicit_runtime_url:
        return explicit_runtime_url

    business_database_url = _raw_business_database_url()
    if not business_database_url:
        return None

    runtime_database_name = os.getenv("RUNTIME_DATABASE_NAME", "manager").strip() or "manager"
    return make_url(business_database_url).set(database=runtime_database_name).render_as_string(
        hide_password=False
    )


class Settings(BaseModel):
    app_name: str = os.getenv("APP_NAME", "Text2SQL Backend")
    app_version: str = os.getenv("APP_VERSION", "0.3.0")
    app_env: str = os.getenv("APP_ENV", "dev")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    enable_docs: bool = os.getenv("ENABLE_DOCS", "true").lower() == "true"
    business_database_url: str | None = _raw_business_database_url()
    runtime_database_url: str | None = _resolve_runtime_database_url()
    runtime_database_name: str = os.getenv("RUNTIME_DATABASE_NAME", "manager")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_api_base: str | None = os.getenv("OPENAI_API_BASE")
    openai_model: str = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL", "stub")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    sql_repair_max_retries: int = int(os.getenv("SQL_REPAIR_MAX_RETRIES", "1"))
    classification_llm_enabled: bool = os.getenv("CLASSIFICATION_LLM_ENABLED", "true").lower() == "true"
    vector_retrieval_provider: str = os.getenv("VECTOR_RETRIEVAL_PROVIDER", "local")
    vector_api_key: str | None = os.getenv("VECTOR_API_KEY") or os.getenv("OPENAI_API_KEY")
    vector_api_base: str | None = os.getenv("VECTOR_API_BASE") or os.getenv("OPENAI_API_BASE")
    vector_model: str = os.getenv("VECTOR_MODEL", "text-embedding-3-small")
    vector_dimensions: int = int(os.getenv("VECTOR_DIMENSIONS", "256"))
    vector_top_k: int = int(os.getenv("VECTOR_TOP_K", "3"))
    vector_timeout_seconds: int = int(os.getenv("VECTOR_TIMEOUT_SECONDS", "20"))
    sql_timeout_seconds: int = int(os.getenv("SQL_TIMEOUT_SECONDS", "30"))
    default_sql_limit: int = int(os.getenv("DEFAULT_SQL_LIMIT", "200"))
    high_risk_sql_limit: int = int(os.getenv("HIGH_RISK_SQL_LIMIT", "1000"))
    execution_cache_ttl_seconds: int = int(os.getenv("EXECUTION_CACHE_TTL_SECONDS", "30"))
    execution_cache_max_entries: int = int(os.getenv("EXECUTION_CACHE_MAX_ENTRIES", "256"))
    execution_max_rows: int = int(os.getenv("EXECUTION_MAX_ROWS", "500"))
    slow_query_threshold_ms: int = int(os.getenv("SLOW_QUERY_THRESHOLD_MS", "3000"))
    auth_token_secret: str = os.getenv("AUTH_TOKEN_SECRET", "dev-token-secret-change-me")
    auth_token_ttl_seconds: int = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "28800"))


settings = Settings()
