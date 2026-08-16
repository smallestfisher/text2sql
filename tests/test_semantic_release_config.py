from __future__ import annotations

from datetime import datetime
import unittest

from backend.app.models.semantic_config import PhysicalCatalogRecord, SemanticAssetDraftRecord
from backend.app.services.metadata_registry import ASSET_NAMES, MetadataRegistry
from backend.app.services.semantic_config_service import (
    SEMANTIC_ASSET_DEFAULTS,
    SemanticConfigService,
)


class PartialAssetSource:
    def read_all(self) -> dict[str, object]:
        return {"tables_metadata": {}}


class DraftRepository:
    def __init__(self) -> None:
        self.catalog: PhysicalCatalogRecord | None = None
        self.drafts: dict[str, SemanticAssetDraftRecord] = {}

    def get_catalog(self) -> PhysicalCatalogRecord | None:
        return self.catalog

    def list_drafts(self) -> list[SemanticAssetDraftRecord]:
        return list(self.drafts.values())

    def save_draft(
        self,
        *,
        name: str,
        content,
        expected_version: int | None = None,
    ) -> SemanticAssetDraftRecord:
        current = self.drafts.get(name)
        version = current.version + 1 if current else 1
        record = SemanticAssetDraftRecord(
            name=name,
            content=content,
            version=version,
            updated_at=datetime.utcnow(),
        )
        self.drafts[name] = record
        return record


class SemanticReleaseConfigTests(unittest.TestCase):
    def test_release_asset_source_never_falls_back_to_files(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "semantic release is missing required asset",
        ):
            MetadataRegistry(asset_source=PartialAssetSource())

    def test_validation_registry_can_start_without_fixture_files(self) -> None:
        registry = MetadataRegistry(
            paths={},
            documents=SEMANTIC_ASSET_DEFAULTS,
        )

        self.assertEqual(registry.tables_metadata, {})
        self.assertEqual(registry.examples_template, [])
        self.assertEqual(registry.business_knowledge_entries, [])
        self.assertEqual(registry.join_patterns, [])

    def test_drafts_are_created_only_after_schema_sync_and_start_empty(self) -> None:
        repository = DraftRepository()
        service = SemanticConfigService(
            repository=repository,
            business_connector=object(),
            schema_scope=["APP"],
            metadata_registry=MetadataRegistry(documents=SEMANTIC_ASSET_DEFAULTS),
        )

        self.assertEqual(service.ensure_drafts(), [])
        self.assertEqual(repository.drafts, {})

        repository.catalog = PhysicalCatalogRecord(
            schema_scope=["APP"],
            status="ready",
            catalog={"tables_metadata": {}},
            catalog_hash="catalog-hash",
            updated_at=datetime.utcnow(),
        )
        drafts = service.ensure_drafts()

        self.assertEqual([draft.name for draft in drafts], list(ASSET_NAMES))
        self.assertEqual(
            {draft.name: draft.content for draft in drafts},
            SEMANTIC_ASSET_DEFAULTS,
        )


if __name__ == "__main__":
    unittest.main()
