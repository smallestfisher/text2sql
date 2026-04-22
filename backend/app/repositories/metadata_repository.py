from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import (
    EXAMPLES_TEMPLATE_PATH,
    QUERY_PLAN_SCHEMA_PATH,
    SEMANTIC_LAYER_PATH,
    SEMANTIC_VIEW_DRAFTS_PATH,
    SESSION_STATE_SCHEMA_PATH,
)


class FileMetadataRepository:
    def __init__(self) -> None:
        self.paths = {
            "semantic_layer": SEMANTIC_LAYER_PATH,
            "examples_template": EXAMPLES_TEMPLATE_PATH,
            "query_plan_schema": QUERY_PLAN_SCHEMA_PATH,
            "session_state_schema": SESSION_STATE_SCHEMA_PATH,
            "semantic_view_drafts": SEMANTIC_VIEW_DRAFTS_PATH,
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
            with path.open("w", encoding="utf-8") as file:
                json.dump(content, file, ensure_ascii=False, indent=2)
                file.write("\n")
        else:
            path.write_text(str(content), encoding="utf-8")
        return path

    def list_names(self) -> list[str]:
        return sorted(self.paths.keys())

    def _resolve(self, name: str) -> Path:
        if name not in self.paths:
            raise KeyError(name)
        return self.paths[name]
