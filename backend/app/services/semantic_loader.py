from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from backend.app.config import SEMANTIC_LAYER_PATH


class SemanticLayerLoader:
    def __init__(self, semantic_layer_path=SEMANTIC_LAYER_PATH) -> None:
        self.semantic_layer_path = semantic_layer_path

    @lru_cache(maxsize=1)
    def load(self) -> dict[str, Any]:
        with self.semantic_layer_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def summary(self) -> dict[str, Any]:
        semantic_layer = self.load()
        semantic_views = semantic_layer.get("semantic_views", [])
        return {
            "version": semantic_layer["version"],
            "domains": [item["name"] for item in semantic_layer.get("domains", [])],
            "entities": [item["name"] for item in semantic_layer.get("entities", [])],
            "metrics": [item["name"] for item in semantic_layer.get("metrics", [])],
            "semantic_views": [item["name"] for item in semantic_views],
            "semantic_view_details": [
                {
                    "name": item["name"],
                    "purpose": item.get("purpose"),
                    "status": item.get("status", "unspecified"),
                    "implementation_stage": item.get("implementation_stage", "unspecified"),
                    "serves_domains": item.get("serves_domains", []),
                    "source_tables": item.get("source_tables", []),
                    "output_fields": item.get("output_fields", []),
                    "design_notes": item.get("design_notes", []),
                }
                for item in semantic_views
            ],
            "semantic_view_status": {
                item["name"]: item.get("status", "unspecified")
                for item in semantic_views
            },
            "semantic_view_stage": {
                item["name"]: item.get("implementation_stage", "unspecified")
                for item in semantic_views
            },
            "tables": [
                node for node in semantic_layer.get("semantic_graph", {}).get("nodes", [])
            ],
        }
