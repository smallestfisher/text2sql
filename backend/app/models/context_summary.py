from __future__ import annotations

from pydantic import BaseModel, Field


class ContextSummary(BaseModel):
    question_type: str | None = None
    subject_domain: str = "unknown"
    semantic_brief: str | None = None
    tables: list[str] = Field(default_factory=list)
    retrieval_domains: list[str] = Field(default_factory=list)
    retrieval_metrics: list[str] = Field(default_factory=list)
    limit: int | None = None
    need_clarification: bool = False
    clarification_question: str | None = None
    source: str = "retrieval_evidence"
