from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from backend.app.config import (
    BUSINESS_KNOWLEDGE_PATH,
    DOMAIN_CONFIG_PATH,
    EXAMPLES_TEMPLATE_PATH,
    JOIN_PATTERNS_PATH,
    QUERY_PLAN_SCHEMA_PATH,
    SESSION_STATE_SCHEMA_PATH,
    TABLES_METADATA_PATH,
)


class MetadataRegistry:
    def __init__(self, paths: dict[str, Path] | None = None) -> None:
        self.paths = paths or {
            "domain_config": DOMAIN_CONFIG_PATH,
            "business_knowledge": BUSINESS_KNOWLEDGE_PATH,
            "examples_template": EXAMPLES_TEMPLATE_PATH,
            "tables_metadata": TABLES_METADATA_PATH,
            "join_patterns": JOIN_PATTERNS_PATH,
            "query_plan_schema": QUERY_PLAN_SCHEMA_PATH,
            "session_state_schema": SESSION_STATE_SCHEMA_PATH,
        }
        self._cache: dict[str, object] = {}
        self.reload()

    def reload(self) -> None:
        self._cache["examples_template"] = self._read_examples_template(self.paths["examples_template"])
        self._cache["tables_metadata"] = self._read_tables_metadata(self.paths["tables_metadata"])
        self._cache["business_knowledge"] = self._read_business_knowledge(self.paths["business_knowledge"])
        self._cache["join_patterns"] = self._read_join_patterns(self.paths["join_patterns"])

    def read(self, name: str):
        if name in self._cache:
            return deepcopy(self._cache[name])
        path = self._resolve(name)
        if path.suffix == ".json":
            return self._read_json_file(path)
        return path.read_text(encoding="utf-8")

    @property
    def tables_metadata(self) -> dict:
        payload = self._cache.get("tables_metadata", {})
        return deepcopy(payload if isinstance(payload, dict) else {})

    @property
    def business_knowledge_document(self) -> dict:
        payload = self._cache.get("business_knowledge", {})
        return deepcopy(payload if isinstance(payload, dict) else {})

    @property
    def business_knowledge_entries(self) -> list[dict]:
        payload = self.business_knowledge_document
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        return deepcopy(entries if isinstance(entries, list) else [])

    @property
    def examples_template(self) -> list[dict]:
        payload = self._cache.get("examples_template", [])
        return deepcopy(payload if isinstance(payload, list) else [])

    @property
    def join_patterns_document(self) -> dict:
        payload = self._cache.get("join_patterns", {})
        return deepcopy(payload if isinstance(payload, dict) else {})

    @property
    def join_patterns(self) -> list[dict]:
        payload = self.join_patterns_document
        patterns = payload.get("patterns", []) if isinstance(payload, dict) else []
        return deepcopy(patterns if isinstance(patterns, list) else [])

    def _resolve(self, name: str) -> Path:
        if name not in self.paths:
            raise KeyError(name)
        return self.paths[name]

    def _read_json_file(self, path: Path):
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except FileNotFoundError as exc:
            raise RuntimeError(f"required metadata file is missing: {path}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON in metadata file {path}: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"failed to load metadata file {path}: {exc}") from exc
        return payload

    def _read_examples_template(self, path: Path) -> list[dict]:
        payload = self._read_json_file(path)
        if not isinstance(payload, list):
            raise RuntimeError(f"examples_template must be a JSON array: {path}")
        return payload

    def _read_tables_metadata(self, path: Path) -> dict:
        payload = self._read_json_file(path)
        if not isinstance(payload, dict):
            raise RuntimeError(f"tables_metadata must be a JSON object: {path}")
        return payload

    def _read_business_knowledge(self, path: Path) -> dict:
        payload = self._read_json_file(path)
        if not isinstance(payload, dict):
            raise RuntimeError(f"business_knowledge must be a JSON object: {path}")
        entries = payload.get("entries")
        if entries is not None and not isinstance(entries, list):
            raise RuntimeError(f"business_knowledge.entries must be a JSON array: {path}")
        return payload

    def _read_join_patterns(self, path: Path) -> dict:
        payload = self._read_json_file(path)
        if not isinstance(payload, dict):
            raise RuntimeError(f"join_patterns must be a JSON object: {path}")
        patterns = payload.get("patterns")
        if patterns is not None and not isinstance(patterns, list):
            raise RuntimeError(f"join_patterns.patterns must be a JSON array: {path}")
        return payload
