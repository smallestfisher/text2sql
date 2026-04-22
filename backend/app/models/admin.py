from __future__ import annotations

from pydantic import BaseModel

from .example_library import ExampleRecord


class MetadataDocument(BaseModel):
    name: str
    path: str
    content: dict | list | str


class MetadataOverview(BaseModel):
    semantic_version: str | None
    semantic_domains: list[str]
    semantic_views: list[str]
    example_count: int
    trace_count: int


class ExampleCollectionResponse(BaseModel):
    examples: list[ExampleRecord]
    count: int


class ExampleMutationResponse(BaseModel):
    created: bool | None = None
    updated: bool | None = None
    example: ExampleRecord
    count: int | None = None
