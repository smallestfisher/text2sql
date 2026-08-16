from __future__ import annotations

from pathlib import Path

from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.semantic_runtime import SemanticRuntime


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def fixture_metadata_registry() -> MetadataRegistry:
    return MetadataRegistry(
        paths={
            "business_knowledge": FIXTURE_DIR / "business_knowledge.json",
            "examples_template": FIXTURE_DIR / "nl2sql_examples.template.json",
            "tables_metadata": FIXTURE_DIR / "tables.json",
            "join_patterns": FIXTURE_DIR / "join_patterns.json",
        }
    )


def fixture_domain_config(metadata_registry: MetadataRegistry | None = None) -> dict:
    registry = metadata_registry or fixture_metadata_registry()
    return DomainConfigLoader(
        tables_metadata_provider=lambda: registry.tables_metadata,
    ).load()


def fixture_semantic_runtime() -> tuple[dict, MetadataRegistry, SemanticRuntime]:
    registry = fixture_metadata_registry()
    domain_config = fixture_domain_config(registry)
    runtime = SemanticRuntime(domain_config, metadata_registry=registry)
    return domain_config, registry, runtime
