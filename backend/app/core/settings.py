from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


def _coerce_str(raw: str) -> str:
    return raw


def _coerce_int(raw: str) -> int:
    return int(raw)


def _coerce_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_optional_bool(raw: str) -> bool | None:
    if raw.strip() == "":
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# type name -> (coercer, whether an empty string is a meaningful value)
_COERCERS: dict[str, Callable[[str], object]] = {
    "str": _coerce_str,
    "int": _coerce_int,
    "bool": _coerce_bool,
    "optional_bool": _coerce_optional_bool,
}


@dataclass(frozen=True)
class FieldSpec:
    """Describes one deployment setting and its environment variable mapping."""

    attr: str
    env: str
    type: str
    group: str
    default: object
    editable: bool = False
    secret: bool = False

    def coerce(self, raw: str) -> object:
        return _COERCERS[self.type](raw)


# The single source of truth for every Settings attribute. Order defines the
# admin-UI ordering within each group.
FIELD_SPECS: tuple[FieldSpec, ...] = (
    # -- application and local persistence --
    FieldSpec("app_name", "APP_NAME", "str", "app", "Text2SQL Backend", editable=False),
    FieldSpec("app_version", "APP_VERSION", "str", "app", "0.3.0", editable=False),
    FieldSpec("app_env", "APP_ENV", "str", "app", "dev", editable=False),
    FieldSpec("log_level", "LOG_LEVEL", "str", "app", "INFO", editable=False),
    FieldSpec("enable_docs", "ENABLE_DOCS", "bool", "app", True, editable=False),
    FieldSpec("runtime_database_url", "RUNTIME_DATABASE_URL", "str", "app", "sqlite:///./runtime/runtime.db", secret=True),
    FieldSpec("vector_cache_dir", "VECTOR_CACHE_DIR", "str", "app", "./runtime/vector-cache"),
    FieldSpec("auth_token_secret", "AUTH_TOKEN_SECRET", "str", "app", "dev-token-secret-change-me", editable=False, secret=True),
    FieldSpec("auth_token_ttl_seconds", "AUTH_TOKEN_TTL_SECONDS", "int", "app", 28800, editable=False),
    # -- business database (deployment-only) --
    FieldSpec(
        "business_database_url",
        "BUSINESS_DATABASE_URL",
        "str",
        "business_db",
        None,
        editable=False,
        secret=True,
    ),
    FieldSpec(
        "business_database_schemas",
        "BUSINESS_DATABASE_SCHEMAS",
        "str",
        "business_db",
        "",
        editable=False,
    ),
    # -- main LLM --
    FieldSpec("openai_api_key", "OPENAI_API_KEY", "str", "llm", None, secret=True),
    FieldSpec("openai_api_base", "OPENAI_API_BASE", "str", "llm", None),
    FieldSpec("llm_model", "LLM_MODEL", "str", "llm", "Qwen/Qwen3-14B"),
    FieldSpec("llm_enable_thinking", "LLM_ENABLE_THINKING", "optional_bool", "llm", None),
    FieldSpec("llm_timeout_seconds", "LLM_TIMEOUT_SECONDS", "int", "llm", 20),
    FieldSpec("llm_max_retries", "LLM_MAX_RETRIES", "int", "llm", 2),
    FieldSpec("sql_repair_max_retries", "SQL_REPAIR_MAX_RETRIES", "int", "llm", 1),
    FieldSpec("llm_cache_ttl_seconds", "LLM_CACHE_TTL_SECONDS", "int", "llm", 300),
    FieldSpec("llm_cache_max_entries", "LLM_CACHE_MAX_ENTRIES", "int", "llm", 256),
    FieldSpec("llm_cache_prompt", "LLM_CACHE_PROMPT", "optional_bool", "llm", None),
    # -- vector retrieval --
    FieldSpec("enable_vector_retrieval", "ENABLE_VECTOR_RETRIEVAL", "bool", "vector", True),
    FieldSpec("prewarm_vector_retrieval", "PREWARM_VECTOR_RETRIEVAL", "bool", "vector", True),
    FieldSpec("vector_retrieval_provider", "VECTOR_RETRIEVAL_PROVIDER", "str", "vector", "siliconflow"),
    FieldSpec("vector_api_key", "VECTOR_API_KEY", "str", "vector", None, secret=True),
    FieldSpec("vector_api_base", "VECTOR_API_BASE", "str", "vector", None),
    FieldSpec("vector_model", "VECTOR_MODEL", "str", "vector", "Qwen/Qwen3-Embedding-8B"),
    FieldSpec("vector_dimensions", "VECTOR_DIMENSIONS", "int", "vector", 1024),
    FieldSpec("vector_top_k", "VECTOR_TOP_K", "int", "vector", 8),
    FieldSpec("vector_timeout_seconds", "VECTOR_TIMEOUT_SECONDS", "int", "vector", 20),
    # -- SQL / execution governance --
    FieldSpec("sql_timeout_seconds", "SQL_TIMEOUT_SECONDS", "int", "sql", 30),
    FieldSpec("default_sql_limit", "DEFAULT_SQL_LIMIT", "int", "sql", 200),
    FieldSpec("high_risk_sql_limit", "HIGH_RISK_SQL_LIMIT", "int", "sql", 1000),
    FieldSpec("execution_cache_ttl_seconds", "EXECUTION_CACHE_TTL_SECONDS", "int", "sql", 30),
    FieldSpec("execution_cache_max_entries", "EXECUTION_CACHE_MAX_ENTRIES", "int", "sql", 256),
    FieldSpec("execution_max_rows", "EXECUTION_MAX_ROWS", "int", "sql", 500),
    FieldSpec("slow_query_threshold_ms", "SLOW_QUERY_THRESHOLD_MS", "int", "sql", 3000),
)

SPEC_BY_ATTR: dict[str, FieldSpec] = {spec.attr: spec for spec in FIELD_SPECS}
SPEC_BY_ENV: dict[str, FieldSpec] = {spec.env: spec for spec in FIELD_SPECS}


class Settings(BaseModel):
    app_name: str = "Text2SQL Backend"
    app_version: str = "0.3.0"
    app_env: str = "dev"
    log_level: str = "INFO"
    enable_docs: bool = True
    business_database_url: str | None = None
    business_database_schemas: str = ""
    runtime_database_url: str = "sqlite:///./runtime/runtime.db"
    vector_cache_dir: str = "./runtime/vector-cache"
    openai_api_key: str | None = None
    openai_api_base: str | None = None
    llm_model: str = "Qwen/Qwen3-14B"
    llm_enable_thinking: bool | None = None
    llm_timeout_seconds: int = 20
    llm_max_retries: int = 2
    sql_repair_max_retries: int = 1
    llm_cache_ttl_seconds: int = 300
    llm_cache_max_entries: int = 256
    llm_cache_prompt: bool | None = None
    enable_vector_retrieval: bool = True
    prewarm_vector_retrieval: bool = True
    vector_retrieval_provider: str = "siliconflow"
    vector_api_key: str | None = None
    vector_api_base: str | None = None
    vector_model: str = "Qwen/Qwen3-Embedding-8B"
    vector_dimensions: int = 1024
    vector_top_k: int = 8
    vector_timeout_seconds: int = 20
    sql_timeout_seconds: int = 30
    default_sql_limit: int = 200
    high_risk_sql_limit: int = 1000
    execution_cache_ttl_seconds: int = 30
    execution_cache_max_entries: int = 256
    execution_max_rows: int = 500
    slow_query_threshold_ms: int = 3000
    auth_token_secret: str = "dev-token-secret-change-me"
    auth_token_ttl_seconds: int = 28800

    @classmethod
    def build(cls) -> "Settings":
        """Construct effective settings from environment variables and defaults."""
        values: dict[str, object] = {}
        for spec in FIELD_SPECS:
            env_value = os.getenv(spec.env)
            raw = env_value if env_value is not None and env_value.strip() != "" else None
            if raw is None:
                values[spec.attr] = spec.default
                continue
            try:
                values[spec.attr] = spec.coerce(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid value for {spec.env}: {raw!r} ({exc})") from exc

        # Post-processing that the previous per-field env parsing applied.
        if isinstance(values.get("log_level"), str):
            values["log_level"] = values["log_level"].upper()
        # vector_api_base defaults to the siliconflow endpoint when the provider
        # is siliconflow and no explicit base was given.
        if not values.get("vector_api_base") and values.get("vector_retrieval_provider") == "siliconflow":
            values["vector_api_base"] = "https://api.siliconflow.cn/v1"

        return cls(**values)

    def business_schema_scope(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.business_database_schemas.replace("，", ",").split(",")
            if item.strip()
        ]


settings = Settings.build()
