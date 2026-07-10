from __future__ import annotations

import json
from pathlib import Path

from backend.app.services.metadata_registry import ASSET_NAMES, MetadataRegistry
from backend.app.utils import atomic_write_text


class MetadataDocumentRepository:
    """Read/write semantic metadata documents through MetadataRegistry.

    Persistence is not always the JSON files under semantic/examples: when the
    registry has an ``asset_source`` (runtime DB store), the four semantic assets
    are upserted there and the path on disk is only returned for API display.
    Eval/other path-based documents still write files. Callers should treat this
    as the document write facade, not a pure filesystem repository.
    """

    def __init__(self, metadata_registry: MetadataRegistry | None = None) -> None:
        self.metadata_registry = metadata_registry or MetadataRegistry()
        self.paths = dict(self.metadata_registry.paths)

    def read(self, name: str):
        return self.metadata_registry.read(name)

    def write(self, name: str, content) -> Path:
        path = self.resolve_path(name)
        # When the registry is backed by a DB asset source, the four semantic
        # assets are persisted to the store instead of the JSON files; the file
        # path is still returned for informational use by MetadataDocument.
        asset_source = self.metadata_registry.asset_source
        if asset_source is not None and name in ASSET_NAMES:
            content = self.metadata_registry.validate(name, content)
            asset_source.upsert(name, content)
            self.metadata_registry.reload()
            return path
        if path.suffix == ".json":
            content = self.metadata_registry.validate(name, content)
            atomic_write_text(
                path,
                json.dumps(content, ensure_ascii=False, indent=2) + '\n',
            )
        else:
            atomic_write_text(path, str(content))
        self.metadata_registry.reload()
        return path

    def list_names(self) -> list[str]:
        return sorted(self.paths.keys())

    def resolve_path(self, name: str) -> Path:
        if name not in self.paths:
            raise KeyError(name)
        return self.paths[name]


# Backward-compatible alias: historical name implied "files only".
FileMetadataRepository = MetadataDocumentRepository
