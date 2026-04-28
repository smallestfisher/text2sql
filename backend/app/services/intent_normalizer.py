from __future__ import annotations

from typing import Any

from backend.app.models.intent import StructuredIntent
from backend.app.models.query_plan import QueryPlan
from backend.app.services.semantic_runtime import SemanticRuntime


class IntentNormalizer:
    def __init__(self, semantic_runtime: SemanticRuntime) -> None:
        self.semantic_runtime = semantic_runtime

    def normalize(self, intent: StructuredIntent) -> dict[str, Any]:
        normalized = intent.model_copy(deep=True)
        normalized.source = "normalized"
        warnings: list[str] = []

        normalized.subject_domain = self._normalize_domain(normalized.subject_domain)
        normalized.metrics, metric_warnings = self._normalize_metrics(
            normalized.metrics,
            normalized.subject_domain,
        )
        warnings.extend(metric_warnings)

        normalized.dimensions, dimension_warnings = self._normalize_dimensions(
            normalized.dimensions,
            normalized.subject_domain,
        )
        warnings.extend(dimension_warnings)

        normalized.filters, filter_warnings = self._normalize_filters(
            normalized.filters,
            normalized.subject_domain,
        )
        warnings.extend(filter_warnings)

        normalized.analysis_mode = self._normalize_analysis_mode(normalized.analysis_mode)
        normalized.question_type = self._normalize_question_type(normalized.question_type)
        normalized.confidence = self._normalize_confidence(normalized.confidence)
        normalized.raw_payload = {
            **normalized.raw_payload,
            "normalizer_warnings": warnings,
        }

        return {
            "status": "completed",
            "intent": normalized,
            "warnings": warnings,
        }

    def _normalize_domain(self, subject_domain: str) -> str:
        if self.semantic_runtime.is_known_domain(subject_domain):
            return subject_domain
        return "unknown"

    def _normalize_metrics(self, metrics: list[str], subject_domain: str) -> tuple[list[str], list[str]]:
        normalized_metrics = self.semantic_runtime.resolve_metrics(
            question="",
            matched_metrics=metrics,
            filters=[],
        )
        if subject_domain == "unknown":
            return normalized_metrics, []
        plan_probe = QueryPlan(question_type="new", subject_domain=subject_domain, metrics=normalized_metrics)
        allowed_fields = self.semantic_runtime.allowed_fields_for_plan(plan_probe)
        kept: list[str] = []
        warnings: list[str] = []
        for metric_name in normalized_metrics:
            metric_field = self.semantic_runtime.metric_column(metric_name)
            if metric_field in allowed_fields:
                kept.append(metric_name)
            else:
                warnings.append(f"drop metric outside domain: {metric_name}")
        return kept, warnings

    def _normalize_dimensions(self, dimensions: list[str], subject_domain: str) -> tuple[list[str], list[str]]:
        if subject_domain == "unknown":
            return [], ([f"drop dimensions for unknown domain: {', '.join(dimensions)}"] if dimensions else [])
        allowed_fields = set(self.semantic_runtime.profile_allowed_fields(subject_domain))
        kept = [item for item in dimensions if item in allowed_fields]
        dropped = [item for item in dimensions if item not in allowed_fields]
        warnings = [f"drop unsupported dimension: {item}" for item in dropped]
        return list(dict.fromkeys(kept)), warnings

    def _normalize_filters(self, filters: list, subject_domain: str):
        if subject_domain == "unknown":
            return [], ([f"drop filters for unknown domain: {len(filters)}"] if filters else [])
        allowed_fields = set(self.semantic_runtime.profile_allowed_fields(subject_domain))
        kept = [item for item in filters if item.field in allowed_fields]
        dropped = [item.field for item in filters if item.field not in allowed_fields]
        warnings = [f"drop unsupported filter field: {field}" for field in dropped]
        return kept, warnings

    def _normalize_analysis_mode(self, analysis_mode: str | None) -> str | None:
        if analysis_mode in {"trend", "compare", "ranking", "summary"}:
            return analysis_mode
        return None

    def _normalize_question_type(self, question_type: str | None) -> str | None:
        if question_type in {"new", "follow_up", "new_related", "new_unrelated", "clarification_needed", "invalid"}:
            return question_type
        return None

    def _normalize_confidence(self, confidence: float | None) -> float | None:
        if confidence is None:
            return None
        return max(0.0, min(1.0, confidence))
