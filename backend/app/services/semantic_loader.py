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
        return {
            "version": semantic_layer["version"],
            "domains": [item["name"] for item in semantic_layer.get("domains", [])],
            "entities": [item["name"] for item in semantic_layer.get("entities", [])],
            "metrics": [item["name"] for item in semantic_layer.get("metrics", [])],
            "semantic_views": [item["name"] for item in semantic_layer.get("semantic_views", [])],
            "tables": [
                node for node in semantic_layer.get("semantic_graph", {}).get("nodes", [])
            ],
        }
