from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import (
    JOIN_PATTERNS_PATH,
    TABLES_METADATA_PATH,
)
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.utils import atomic_write_text


class FileMetadataRepository:
    def __init__(self, metadata_registry: MetadataRegistry | None = None) -> None:
        self.metadata_registry = metadata_registry or MetadataRegistry()
        self.paths = dict(self.metadata_registry.paths)
        self.paths["tables_metadata"] = TABLES_METADATA_PATH
        self.paths["join_patterns"] = JOIN_PATTERNS_PATH

    def read(self, name: str):
        return self.metadata_registry.read(name)

    def write(self, name: str, content) -> Path:
        path = self._resolve(name)
        if path.suffix == ".json":
            atomic_write_text(
                path,
                json.dumps(content, ensure_ascii=False, indent=2) + "\n",
            )
        else:
            atomic_write_text(path, str(content))
        self.metadata_registry.reload()
        return path

    def list_names(self) -> list[str]:
        return sorted(self.paths.keys())

    def _resolve(self, name: str) -> Path:
        if name not in self.paths:
            raise KeyError(name)
        return self.paths[name]
