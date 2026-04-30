from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import (
    BUSINESS_KNOWLEDGE_PATH,
    EXAMPLES_TEMPLATE_PATH,
    QUERY_PLAN_SCHEMA_PATH,
    DOMAIN_CONFIG_PATH,
    SESSION_STATE_SCHEMA_PATH,
)
from backend.app.utils import atomic_write_text


class FileMetadataRepository:
    def __init__(self) -> None:
        self.paths = {
            "domain_config": DOMAIN_CONFIG_PATH,
            "business_knowledge": BUSINESS_KNOWLEDGE_PATH,
            "examples_template": EXAMPLES_TEMPLATE_PATH,
            "query_plan_schema": QUERY_PLAN_SCHEMA_PATH,
            "session_state_schema": SESSION_STATE_SCHEMA_PATH,
        }

    def read(self, name: str):
        path = self._resolve(name)
        if path.suffix == ".json":
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        return path.read_text(encoding="utf-8")

    def write(self, name: str, content) -> Path:
        path = self._resolve(name)
        if path.suffix == ".json":
            atomic_write_text(
                path,
                json.dumps(content, ensure_ascii=False, indent=2) + "\n",
            )
        else:
            atomic_write_text(path, str(content))
        return path

    def list_names(self) -> list[str]:
        return sorted(self.paths.keys())

    def _resolve(self, name: str) -> Path:
        if name not in self.paths:
            raise KeyError(name)
        return self.paths[name]
