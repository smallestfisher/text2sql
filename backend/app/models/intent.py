from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .classification import QueryIntent
from .query_plan import FilterItem, SortItem, SubjectDomain, TimeContext, VersionContext


IntentSource = Literal["parser", "llm", "normalized"]


class StructuredIntent(BaseModel):
    source: IntentSource
    normalized_question: str
    subject_domain: SubjectDomain = "unknown"
    metrics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterItem] = Field(default_factory=list)
    time_context: TimeContext = Field(default_factory=TimeContext)
    version_context: VersionContext | None = None
    analysis_mode: str | None = None
    question_type: str | None = None
    inherit_context: bool | None = None
    confidence: float | None = None
    reason: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_query_intent(cls, query_intent: QueryIntent) -> "StructuredIntent":
        return cls(
            source="parser",
            normalized_question=query_intent.normalized_question,
            subject_domain=query_intent.subject_domain,
            metrics=list(query_intent.matched_metrics),
            entities=list(query_intent.matched_entities),
            dimensions=list(query_intent.requested_dimensions),
            filters=list(query_intent.filters),
            time_context=query_intent.time_context.model_copy(deep=True),
            version_context=(
                query_intent.version_context.model_copy(deep=True)
                if query_intent.version_context is not None
                else None
            ),
            analysis_mode=query_intent.analysis_mode,
            raw_payload={
                "has_follow_up_cue": query_intent.has_follow_up_cue,
                "has_explicit_slots": query_intent.has_explicit_slots,
            },
        )

    @classmethod
    def from_llm_payload(
        cls,
        normalized_question: str,
        payload: dict[str, Any],
    ) -> "StructuredIntent":
        filters = cls._parse_filters(payload.get("filters"))
        time_context = cls._parse_time_context(payload.get("time_context"))
        version_context = cls._parse_version_context(payload.get("version_context"))
        return cls(
            source="llm",
            normalized_question=normalized_question,
            subject_domain=cls._parse_subject_domain(payload.get("subject_domain")),
            metrics=cls._parse_string_list(payload.get("metrics")),
            entities=cls._parse_string_list(payload.get("entities")),
            dimensions=cls._parse_string_list(payload.get("dimensions")),
            filters=filters,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=cls._parse_optional_string(payload.get("analysis_mode")),
            question_type=cls._parse_optional_string(payload.get("question_type")),
            inherit_context=payload.get("inherit_context") if isinstance(payload.get("inherit_context"), bool) else None,
            confidence=cls._parse_confidence(payload.get("confidence")),
            reason=cls._parse_optional_string(payload.get("reason")),
            raw_payload=dict(payload),
        )

    @staticmethod
    def _parse_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(items))

    @staticmethod
    def _parse_optional_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _parse_subject_domain(value: Any) -> SubjectDomain:
        allowed = {
            "inventory",
            "demand",
            "plan_actual",
            "sales_financial",
            "dimension",
            "unknown",
        }
        if isinstance(value, str) and value in allowed:
            return value  # type: ignore[return-value]
        return "unknown"

    @staticmethod
    def _parse_filters(value: Any) -> list[FilterItem]:
        if not isinstance(value, list):
            return []
        filters: list[FilterItem] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                filters.append(FilterItem.model_validate(item))
            except Exception:
                continue
        return filters

    @staticmethod
    def _parse_time_context(value: Any) -> TimeContext:
        if not isinstance(value, dict):
            return TimeContext()
        try:
            return TimeContext.model_validate(value)
        except Exception:
            return TimeContext()

    @staticmethod
    def _parse_version_context(value: Any) -> VersionContext | None:
        if not isinstance(value, dict):
            return None
        try:
            return VersionContext.model_validate(value)
        except Exception:
            return None

    @staticmethod
    def _parse_confidence(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        return None

    def to_query_intent(self, base_query_intent: QueryIntent | None = None) -> QueryIntent:
        base = base_query_intent
        matched_metrics = self.metrics or (list(base.matched_metrics) if base is not None else [])
        matched_entities = self.entities or (list(base.matched_entities) if base is not None else [])
        requested_dimensions = self.dimensions or (list(base.requested_dimensions) if base is not None else [])
        filters = self._merge_filters(base.filters if base is not None else [], self.filters)
        time_context = self.time_context
        if base is not None and self.time_context.grain == "unknown":
            time_context = base.time_context.model_copy(deep=True)
        version_context = self.version_context
        if version_context is None and base is not None and base.version_context is not None:
            version_context = base.version_context.model_copy(deep=True)
        requested_sort = list(base.requested_sort) if base is not None else []
        requested_limit = base.requested_limit if base is not None else None
        subject_domain = self.subject_domain
        if subject_domain == "unknown" and base is not None:
            subject_domain = base.subject_domain
        analysis_mode = self.analysis_mode or (base.analysis_mode if base is not None else None)
        has_follow_up_cue = base.has_follow_up_cue if base is not None else False
        has_explicit_slots = bool(
            matched_metrics
            or requested_dimensions
            or filters
            or time_context.grain != "unknown"
            or version_context is not None
            or requested_sort
            or requested_limit is not None
            or analysis_mode is not None
            or (base.has_explicit_slots if base is not None else False)
        )
        return QueryIntent(
            normalized_question=self.normalized_question,
            matched_metrics=matched_metrics,
            matched_entities=matched_entities,
            requested_dimensions=requested_dimensions,
            filters=filters,
            time_context=time_context,
            version_context=version_context,
            requested_sort=requested_sort,
            requested_limit=requested_limit,
            analysis_mode=analysis_mode,
            subject_domain=subject_domain,
            has_follow_up_cue=has_follow_up_cue,
            has_explicit_slots=has_explicit_slots,
        )

    @staticmethod
    def _merge_filters(base_filters: list[FilterItem], new_filters: list[FilterItem]) -> list[FilterItem]:
        merged: dict[str, FilterItem] = {}
        for item in base_filters:
            merged[StructuredIntent._filter_key(item)] = item
        for item in new_filters:
            merged[StructuredIntent._filter_key(item)] = item
        return list(merged.values())

    @staticmethod
    def _filter_key(filter_item: FilterItem) -> str:
        return f"{filter_item.field}:{filter_item.op}"
