from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from backend.app.config import DOMAIN_CONFIG_PATH


class DomainConfigLoader:
    def __init__(self, domain_config_path=DOMAIN_CONFIG_PATH) -> None:
        self.domain_config_path = domain_config_path

    @lru_cache(maxsize=1)
    def load(self) -> dict[str, Any]:
        with self.domain_config_path.open("r", encoding="utf-8") as file:
            return json.load(file)

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
