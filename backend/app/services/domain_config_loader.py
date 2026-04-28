from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.app.config import DOMAIN_CONFIG_PATH


class DomainConfigLoader:
    """Loads the semantic config manifest and merges included fragments."""

    def __init__(self, domain_config_path=DOMAIN_CONFIG_PATH) -> None:
        self.domain_config_path = domain_config_path

    @lru_cache(maxsize=1)
    def load(self) -> dict[str, Any]:
        return self._load_document(self.domain_config_path, visited=())

    def summary(self) -> dict[str, Any]:
        domain_config = self.load()
        return {
            "version": domain_config["version"],
            "domains": [item["name"] for item in domain_config.get("domains", [])],
            "entities": [item["name"] for item in domain_config.get("entities", [])],
            "metrics": [item["name"] for item in domain_config.get("metrics", [])],
            "tables": [
                node for node in domain_config.get("semantic_graph", {}).get("nodes", [])
            ],
        }

    def _load_document(
        self,
        path: Path,
        *,
        visited: tuple[Path, ...],
    ) -> dict[str, Any]:
        resolved_path = path.resolve()
        if resolved_path in visited:
            cycle = " -> ".join(str(item) for item in (*visited, resolved_path))
            raise ValueError(f"domain config include cycle detected: {cycle}")

        with resolved_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, dict):
            raise ValueError(f"domain config fragment must be a JSON object: {resolved_path}")

        includes = payload.pop("$includes", [])
        merged: dict[str, Any] = payload
        if includes and not isinstance(includes, list):
            raise ValueError(f"domain config $includes must be a list: {resolved_path}")

        for include in includes:
            if not isinstance(include, str) or not include.strip():
                raise ValueError(f"domain config include must be a non-empty string: {resolved_path}")
            # Includes are resolved relative to the manifest or fragment file itself.
            included_path = (resolved_path.parent / include).resolve()
            included_payload = self._load_document(included_path, visited=(*visited, resolved_path))
            merged = self._merge_values(merged, included_payload, path=included_path)

        return merged

    def _merge_values(
        self,
        base: Any,
        incoming: Any,
        *,
        path: Path,
    ) -> Any:
        if isinstance(base, dict) and isinstance(incoming, dict):
            merged = dict(base)
            for key, value in incoming.items():
                if key not in merged:
                    merged[key] = value
                    continue
                merged[key] = self._merge_values(merged[key], value, path=path)
            return merged

        if isinstance(base, list) and isinstance(incoming, list):
            return [*base, *incoming]

        if base == incoming:
            return base

        raise ValueError(
            f"domain config fragment conflict at {path}: "
            f"cannot merge {type(base).__name__} with {type(incoming).__name__}"
        )
