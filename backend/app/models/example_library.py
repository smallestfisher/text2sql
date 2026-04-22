from __future__ import annotations

from pydantic import BaseModel, Field

from .query_plan import FilterItem, QuestionType, SubjectDomain


class ExampleRecord(BaseModel):
    id: str
    question: str
    normalized_question: str
    intent: str
    subject_domain: SubjectDomain
    question_type: QuestionType
    tables: list[str] = Field(default_factory=list)
    semantic_views: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    join_path: list[str] = Field(default_factory=list)
    sql: str
    result_shape: str | None = None
    notes: str | None = None
