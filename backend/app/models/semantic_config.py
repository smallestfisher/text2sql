from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CatalogSyncStatus = Literal["not_synced", "syncing", "ready", "error"]
SemanticReleaseStatus = Literal["building", "active", "inactive", "failed"]


class DatabaseStatusRecord(BaseModel):
    configured: bool
    connected: bool
    dialect: str = "oracle"
    schema_scope: list[str] = Field(default_factory=list)
    sync_status: CatalogSyncStatus = "not_synced"
    table_count: int = 0
    column_count: int = 0
    relationship_count: int = 0
    catalog_hash: str | None = None
    active_release_id: str | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None


class SchemaSyncRequest(BaseModel):
    include_views: bool = True
    sample_values_per_column: int = Field(default=0, ge=0, le=20)


class PhysicalCatalogRecord(BaseModel):
    schema_scope: list[str] = Field(default_factory=list)
    status: CatalogSyncStatus
    catalog: dict = Field(default_factory=dict)
    catalog_hash: str | None = None
    warnings: list[str] = Field(default_factory=list)
    last_error: str | None = None
    synced_at: datetime | None = None
    updated_at: datetime


class SchemaSyncResponse(BaseModel):
    database: DatabaseStatusRecord
    catalog: PhysicalCatalogRecord
    draft_version: int
    warnings: list[str] = Field(default_factory=list)


class SemanticAssetDraftRecord(BaseModel):
    name: str
    content: dict | list | str
    version: int
    updated_at: datetime


class SemanticAssetDraftUpdateRequest(BaseModel):
    content: dict | list | str
    expected_version: int | None = Field(default=None, ge=1)


class SemanticDraftUpdateResponse(BaseModel):
    draft: SemanticAssetDraftRecord
    warnings: list[str] = Field(default_factory=list)


class SemanticDraftCollectionResponse(BaseModel):
    drafts: list[SemanticAssetDraftRecord]
    count: int


class SemanticReleaseRecord(BaseModel):
    id: str
    version: int
    status: SemanticReleaseStatus
    catalog_hash: str
    created_by: str | None = None
    error: str | None = None
    created_at: datetime
    activated_at: datetime | None = None


class SemanticReleaseDetailRecord(SemanticReleaseRecord):
    snapshot: dict
    draft_versions: dict[str, int] = Field(default_factory=dict)


class SemanticReleaseCollectionResponse(BaseModel):
    releases: list[SemanticReleaseRecord]
    count: int


class SemanticReleaseDetailResponse(BaseModel):
    release: SemanticReleaseDetailRecord


class SemanticPublishResponse(BaseModel):
    release: SemanticReleaseDetailRecord
    warnings: list[str] = Field(default_factory=list)
