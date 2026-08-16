from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

ASSET_NAMES: tuple[str, ...] = (
    "examples_template",
    "tables_metadata",
    "business_knowledge",
    "join_patterns",
)


def _empty_asset(name: str) -> object:
    return {
        "tables_metadata": {},
        "business_knowledge": {"entries": []},
        "join_patterns": {"patterns": []},
        "examples_template": [],
    }[name]


class MetadataRegistry:
    def __init__(
        self,
        paths: dict[str, Path] | None = None,
        asset_source=None,
        documents: dict[str, object] | None = None,
    ) -> None:
        self.paths = dict(paths or {})
        self.asset_source = asset_source
        self.documents = documents
        if self.documents is None and self.asset_source is None and not self.paths:
            raise ValueError("metadata registry requires release assets, explicit documents, or explicit fixture paths")
        self._cache: dict[str, object] = {}
        self.reload()

    def reload(self) -> None:
        if self.documents is not None:
            self._cache = {
                name: self.validate(name, self.documents.get(name, _empty_asset(name)))
                for name in ASSET_NAMES
            }
        elif self.asset_source is not None:
            self._cache = self._reload_from_source()
            return
        else:
            self._cache = self._reload_from_files()

    def _reload_from_files(self) -> dict[str, object]:
        missing = [name for name in ASSET_NAMES if name not in self.paths]
        if missing:
            raise ValueError("fixture metadata paths are missing: " + ", ".join(missing))
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
                raise RuntimeError(f"semantic release is missing required asset: {name}")
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

    @property
    def subject_domains(self) -> list[str]:
        domains: list[str] = []

        def add(value: object) -> None:
            if not isinstance(value, str):
                return
            normalized = value.strip()
            if normalized and normalized != "unknown" and normalized not in domains:
                domains.append(normalized)

        for example in self.examples_template:
            if isinstance(example, dict):
                add(example.get("subject_domain"))
        for entry in self.business_knowledge_entries:
            if not isinstance(entry, dict):
                continue
            for domain in entry.get("domains", []) or []:
                add(domain)
        for pattern in self.join_patterns:
            if not isinstance(pattern, dict):
                continue
            for domain in pattern.get("domains", []) or []:
                add(domain)
        for table in self.tables_metadata.values():
            if not isinstance(table, dict):
                continue
            add(table.get("subject_domain"))
            for domain in table.get("domains", []) or []:
                add(domain)
        return domains

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
