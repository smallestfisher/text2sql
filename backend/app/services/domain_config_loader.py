from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from backend.app.config import TABLES_METADATA_PATH


class DomainConfigLoader:
    """Builds the minimal runtime schema boundary from table metadata.

    Business meaning is supplied as text through business_knowledge, examples
    and join_patterns. This loader only exposes the physical table boundary
    still needed by SQL validation and admin summaries.
    """

    def __init__(
        self,
        tables_metadata_path=TABLES_METADATA_PATH,
        tables_metadata_provider=None,
    ) -> None:
        self.tables_metadata_path = tables_metadata_path
        # When ``tables_metadata_provider`` is supplied (a zero-arg callable
        # returning the tables_metadata dict), the schema boundary is built from
        # it instead of the JSON file. This keeps the boundary aligned with the
        # DB-backed semantic asset store; otherwise the file is read as before.
        self.tables_metadata_provider = tables_metadata_provider

    @lru_cache(maxsize=1)
    def load(self) -> dict[str, Any]:
        tables_metadata = self._load_tables_metadata()
        table_names = list(tables_metadata.keys())
        return {
            "version": "text-context-schema-boundary",
            "domains": [],
            "entities": [],
            "metrics": [],
            "semantic_graph": {
                "nodes": table_names,
                "edges": self._relationship_edges(tables_metadata),
            },
        }

    def clear_cache(self) -> None:
        self.load.cache_clear()

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
        if self.tables_metadata_provider is not None:
            payload = self.tables_metadata_provider()
            if not isinstance(payload, dict):
                raise ValueError("tables metadata provider must return a JSON object")
            return payload
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
