from __future__ import annotations

import logging

from backend.app.repositories.db_semantic_asset_repository import DbSemanticAssetRepository
from backend.app.services.metadata_registry import ASSET_NAMES, MetadataRegistry


logger = logging.getLogger(__name__)


def seed_semantic_assets_if_empty(
    repository: DbSemanticAssetRepository,
) -> bool:
    """Seed the ``semantic_assets`` store from the JSON files when it is empty.

    Returns True when a seed was performed. Once the store holds any asset the
    files are no longer consulted for the runtime path; they remain only as the
    version-controlled seed source and as the fallback for an asset missing
    from a partially seeded store.
    """
    if repository.has_any():
        return False
    file_registry = MetadataRegistry()
    seeded: list[str] = []
    for name in ASSET_NAMES:
        content = file_registry.read(name)
        repository.upsert(name, content)
        seeded.append(name)
    logger.info("semantic assets seeded from files count=%d names=%s", len(seeded), seeded)
    return True
