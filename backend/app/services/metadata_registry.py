from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from backend.app.config import (
    BUSINESS_KNOWLEDGE_PATH,
    EXAMPLES_TEMPLATE_PATH,
    JOIN_PATTERNS_PATH,
    SESSION_STATE_SCHEMA_PATH,
    TABLES_METADATA_PATH,
)


ASSET_NAMES: tuple[str, ...] = (
    "examples_template",
    "tables_metadata",
    "business_knowledge",
    "join_patterns",
)


class MetadataRegistry:
    def __init__(
        self,
        paths: dict[str, Path] | None = None,
        asset_source=None,
    ) -> None:
        self.paths = paths or {
            "business_knowledge": BUSINESS_KNOWLEDGE_PATH,
            "examples_template": EXAMPLES_TEMPLATE_PATH,
            "tables_metadata": TABLES_METADATA_PATH,
            "join_patterns": JOIN_PATTERNS_PATH,
            "session_state_schema": SESSION_STATE_SCHEMA_PATH,
        }
        # When ``asset_source`` is provided (a DbSemanticAssetRepository-like
        # object exposing ``read_all()``), the four semantic assets are read
        # from it instead of the JSON files. Files then act only as the seed
        # source loaded into the store before the registry is constructed.
        # An asset missing from the source falls back to its JSON file so a
        # partially seeded store never breaks startup.
        self.asset_source = asset_source
        self._cache: dict[str, object] = {}
        self.reload()

    def reload(self) -> None:
        if self.asset_source is not None:
            self._cache = self._reload_from_source()
            return
        self._cache = self._reload_from_files()

    def _reload_from_files(self) -> dict[str, object]:
        return {
            "examples_template": self._read_examples_template(self.paths["examples_template"]),
            "tables_metadata": self._read_tables_metadata(self.paths["tables_metadata"]),
            "business_knowledge": self._read_business_knowledge(self.paths["business_knowledge"]),
            "join_patterns": self._read_join_patterns(self.paths["join_patterns"]),
        }

    def _reload_from_source(self) -> dict[str, object]:
        stored = self.asset_source.read_all()
        new_cache: dict[str, object] = {}
        for name in ASSET_NAMES:
            payload = stored.get(name)
            if payload is None:
                new_cache[name] = self._read_asset_file(name)
            else:
                new_cache[name] = self.validate(name, payload)
        return new_cache

    def _read_asset_file(self, name: str):
        if name == "examples_template":
            return self._read_examples_template(self.paths["examples_template"])
        if name == "tables_metadata":
            return self._read_tables_metadata(self.paths["tables_metadata"])
        if name == "business_knowledge":
            return self._read_business_knowledge(self.paths["business_knowledge"])
        if name == "join_patterns":
            return self._read_join_patterns(self.paths["join_patterns"])
        raise KeyError(name)

    def validate(self, name: str, payload):
        if name == "examples_template":
            return self._validate_examples_template(payload, source=name)
        if name == "tables_metadata":
            return self._validate_tables_metadata(payload, source=name)
        if name == "business_knowledge":
            return self._validate_business_knowledge(payload, source=name)
        if name == "join_patterns":
            return self._validate_join_patterns(payload, source=name)
        self._resolve(name)
        return payload

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
    def business_knowledge_entries(self) -> list[dict]:
        payload = self._cache.get("business_knowledge", {})
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        return deepcopy(entries if isinstance(entries, list) else [])

    @property
    def examples_template(self) -> list[dict]:
        payload = self._cache.get("examples_template", [])
        return deepcopy(payload if isinstance(payload, list) else [])

    @property
    def join_patterns(self) -> list[dict]:
        payload = self._cache.get("join_patterns", {})
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
        return self._validate_examples_template(payload, source=str(path))

    def _validate_examples_template(self, payload, *, source: str) -> list[dict]:
        if not isinstance(payload, list):
            raise RuntimeError(f"examples_template must be a JSON array: {source}")
        return payload

    def _read_tables_metadata(self, path: Path) -> dict:
        payload = self._read_json_file(path)
        return self._validate_tables_metadata(payload, source=str(path))

    def _validate_tables_metadata(self, payload, *, source: str) -> dict:
        if not isinstance(payload, dict):
            raise RuntimeError(f"tables_metadata must be a JSON object: {source}")
        return payload

    def _read_business_knowledge(self, path: Path) -> dict:
        payload = self._read_json_file(path)
        return self._validate_business_knowledge(payload, source=str(path))

    def _validate_business_knowledge(self, payload, *, source: str) -> dict:
        if not isinstance(payload, dict):
            raise RuntimeError(f"business_knowledge must be a JSON object: {source}")
        entries = payload.get("entries")
        if entries is not None and not isinstance(entries, list):
            raise RuntimeError(f"business_knowledge.entries must be a JSON array: {source}")
        return payload

    def _read_join_patterns(self, path: Path) -> dict:
        payload = self._read_json_file(path)
        return self._validate_join_patterns(payload, source=str(path))

    def _validate_join_patterns(self, payload, *, source: str) -> dict:
        if not isinstance(payload, dict):
            raise RuntimeError(f"join_patterns must be a JSON object: {source}")
        patterns = payload.get("patterns")
        if patterns is not None and not isinstance(patterns, list):
            raise RuntimeError(f"join_patterns.patterns must be a JSON array: {source}")
        return payload
