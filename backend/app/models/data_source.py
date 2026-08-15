from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


DataSourceStatus = Literal["draft", "ready", "syncing", "error", "disabled"]


class DataSourceScope(BaseModel):
    workspace_id: str = "default"
    domain_id: str = "default"


class DataSourceCreateRequest(DataSourceScope):
    name: str = Field(min_length=1, max_length=191)
    database_url: str = Field(min_length=1)
    schemas: list[str] = Field(default_factory=list)
    description: str | None = None


class DataSourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=191)
    database_url: str | None = None
    schemas: list[str] | None = None
    description: str | None = None
    enabled: bool | None = None


class DataSourceRecord(DataSourceScope):
    id: str
    name: str
    dialect: Literal["oracle"] = "oracle"
    schemas: list[str] = Field(default_factory=list)
    description: str | None = None
    status: DataSourceStatus = "draft"
    enabled: bool = True
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class DataSourceCollectionResponse(BaseModel):
    data_sources: list[DataSourceRecord]
    count: int


class SchemaSyncRequest(BaseModel):
    schemas: list[str] = Field(default_factory=list)
    include_views: bool = True
    sample_values_per_column: int = Field(default=0, ge=0, le=20)


class SchemaSyncResponse(BaseModel):
    data_source: DataSourceRecord
    table_count: int
    column_count: int
    relationship_count: int
    tables_metadata: dict
    warnings: list[str] = Field(default_factory=list)
