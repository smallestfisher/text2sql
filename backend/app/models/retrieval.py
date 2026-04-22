from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievalHit(BaseModel):
    source_type: str
    source_id: str
    score: float
    summary: str
    matched_features: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class RetrievalContext(BaseModel):
    domains: list[str] = Field(default_factory=list)
    semantic_views: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    retrieval_terms: list[str] = Field(default_factory=list)
    hits: list[RetrievalHit] = Field(default_factory=list)
