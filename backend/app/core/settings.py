from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_env_file() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()


def _raw_business_database_url() -> str | None:
    return os.getenv("BUSINESS_DATABASE_URL")


def _default_vector_provider() -> str:
    return os.getenv("VECTOR_RETRIEVAL_PROVIDER", "siliconflow")


def _default_vector_api_base() -> str | None:
    explicit = os.getenv("VECTOR_API_BASE")
    if explicit:
        return explicit
    if _default_vector_provider() == "siliconflow":
        return "https://api.siliconflow.cn/v1"
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_bool(name: str) -> bool | None:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return None
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    app_name: str = os.getenv("APP_NAME", "Text2SQL Backend")
    app_version: str = os.getenv("APP_VERSION", "0.3.0")
    app_env: str = os.getenv("APP_ENV", "dev")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    enable_docs: bool = _env_bool("ENABLE_DOCS", default=True)
    business_database_url: str | None = _raw_business_database_url()
    runtime_database_url: str | None = os.getenv("RUNTIME_DATABASE_URL")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_api_base: str | None = os.getenv("OPENAI_API_BASE")
    llm_model: str = os.getenv("LLM_MODEL", "Qwen/Qwen3-14B")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    sql_repair_max_retries: int = int(os.getenv("SQL_REPAIR_MAX_RETRIES", "1"))
    llm_cache_ttl_seconds: int = int(os.getenv("LLM_CACHE_TTL_SECONDS", "300"))
    llm_cache_max_entries: int = int(os.getenv("LLM_CACHE_MAX_ENTRIES", "256"))
    llm_cache_prompt: bool | None = _env_optional_bool("LLM_CACHE_PROMPT")
    enable_vector_retrieval: bool = _env_bool("ENABLE_VECTOR_RETRIEVAL", default=True)
    prewarm_vector_retrieval: bool = _env_bool("PREWARM_VECTOR_RETRIEVAL", default=True)
    vector_retrieval_provider: str = _default_vector_provider()
    vector_api_key: str | None = os.getenv("VECTOR_API_KEY")
    vector_api_base: str | None = _default_vector_api_base()
    vector_model: str = os.getenv("VECTOR_MODEL", "Qwen/Qwen3-Embedding-8B")
    vector_dimensions: int = int(os.getenv("VECTOR_DIMENSIONS", "1024"))
    vector_top_k: int = int(os.getenv("VECTOR_TOP_K", "8"))
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
