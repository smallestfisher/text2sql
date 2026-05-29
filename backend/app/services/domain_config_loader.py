from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.app.config import TABLES_METADATA_PATH


class DomainConfigLoader:
    """Builds the minimal runtime schema boundary from table metadata.

    The Text2SQL main path no longer loads structured business semantics from
    semantic/domain_config/*. Business meaning is supplied as text through
    business_knowledge, examples and join_patterns; this loader only exposes the
    physical table boundary still needed by SQL validation and legacy admin
    summaries during the migration.
    """

    def __init__(self, tables_metadata_path=TABLES_METADATA_PATH) -> None:
        self.tables_metadata_path = tables_metadata_path

    @lru_cache(maxsize=1)
    def load(self) -> dict[str, Any]:
        tables_metadata = self._load_tables_metadata()
        table_names = list(tables_metadata.keys())
        return {
            "version": "text-context-schema-boundary",
            "domains": [],
            "entities": [],
            "metrics": [],
            "query_profiles": {},
            "question_understanding": {},
            "domain_inference": {},
            "field_semantics": [],
            "extractors": {},
            "prompt_assets": {},
            "semantic_graph": {
                "nodes": table_names,
                "edges": self._relationship_edges(tables_metadata),
            },
        }

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

    def _load_tables_metadata(self) -> dict[str, Any]:
        with self.tables_metadata_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError(f"tables metadata must be a JSON object: {self.tables_metadata_path}")
        return payload

    def _relationship_edges(self, tables_metadata: dict[str, Any]) -> list[dict[str, str]]:
        edges: list[dict[str, str]] = []
        table_names = set(tables_metadata.keys())
        for source_table, payload in tables_metadata.items():
            if not isinstance(payload, dict):
                continue
            relationships = payload.get("relationships", {})
            if not isinstance(relationships, dict):
                continue
            for source_field, raw_targets in relationships.items():
                for target in str(raw_targets or "").split(","):
                    target = target.strip()
                    if not target or "." not in target:
                        continue
                    target_table, target_field = target.split(".", 1)
                    if target_table not in table_names:
                        continue
                    edges.append(
                        {
                            "from": source_table,
                            "to": target_table,
                            "on": f"{source_table}.{source_field} = {target_table}.{target_field}",
                            "source": source_table,
                            "target": target_table,
                            "source_field": str(source_field),
                            "target_field": target_field,
                        }
                    )
        return edges

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
