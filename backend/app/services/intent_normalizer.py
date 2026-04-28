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
        normalized.filters, filter_warnings = self._normalize_filters(
            normalized.filters,
            normalized.subject_domain,
        )
        warnings.extend(filter_warnings)

        normalized.metrics, metric_warnings = self._normalize_metrics(
            normalized.metrics,
            normalized.subject_domain,
            normalized.filters,
        )
        warnings.extend(metric_warnings)

        normalized.dimensions, dimension_warnings = self._normalize_dimensions(
            normalized.dimensions,
            normalized.subject_domain,
        )
        warnings.extend(dimension_warnings)

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

    def _normalize_metrics(
        self,
        metrics: list[str],
        subject_domain: str,
        filters: list,
    ) -> tuple[list[str], list[str]]:
        normalized_metrics: list[str] = []
        warnings: list[str] = []
        for metric_name in metrics:
            resolved = self._resolve_metric_name(metric_name, subject_domain, filters)
            if resolved is None:
                warnings.append(f"drop unsupported metric: {metric_name}")
                continue
            if resolved not in normalized_metrics:
                normalized_metrics.append(resolved)

        normalized_metrics = self.semantic_runtime.resolve_metrics(
            question="",
            matched_metrics=normalized_metrics,
            filters=filters,
        )
        if subject_domain == "unknown":
            return normalized_metrics, warnings
        plan_probe = QueryPlan(
            question_type="new",
            subject_domain=subject_domain,
            metrics=normalized_metrics,
            tables=self.semantic_runtime.resolve_tables_for_plan(subject_domain, normalized_metrics),
        )
        allowed_fields = self.semantic_runtime.allowed_fields_for_plan(plan_probe)
        kept: list[str] = []
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
        allowed_fields = self._domain_allowed_fields(subject_domain)
        kept: list[str] = []
        dropped: list[str] = []
        for item in dimensions:
            normalized_field = self._normalize_domain_field(item, subject_domain)
            if normalized_field in allowed_fields:
                if normalized_field not in kept:
                    kept.append(normalized_field)
                continue
            dropped.append(item)
        warnings = [f"drop unsupported dimension: {item}" for item in dropped]
        return kept, warnings

    def _normalize_filters(self, filters: list, subject_domain: str):
        if subject_domain == "unknown":
            return [], ([f"drop filters for unknown domain: {len(filters)}"] if filters else [])
        allowed_fields = self._domain_allowed_fields(subject_domain)
        kept = []
        dropped = []
        for item in filters:
            normalized_field = self._normalize_domain_field(item.field, subject_domain)
            if normalized_field not in allowed_fields:
                dropped.append(item.field)
                continue
            kept.append(item.model_copy(update={"field": normalized_field}))
        warnings = [f"drop unsupported filter field: {field}" for field in dropped]
        return kept, warnings

    def _domain_allowed_fields(self, subject_domain: str) -> set[str]:
        allowed_fields = set(self.semantic_runtime.profile_allowed_fields(subject_domain))
        for table_name in self.semantic_runtime.domain_tables(subject_domain):
            allowed_fields.update(self.semantic_runtime.table_fields(table_name))
        return allowed_fields

    def _normalize_domain_field(self, field_name: str, subject_domain: str) -> str:
        if not field_name:
            return field_name
        if field_name in self.semantic_runtime.profile_allowed_fields(subject_domain):
            return field_name
        lowered = field_name.lower()
        for logical_field, aliases in self.semantic_runtime.profile_field_aliases(subject_domain).items():
            candidates = [logical_field, *aliases]
            if any(candidate.lower() == lowered for candidate in candidates if candidate):
                return logical_field
        return field_name

    def _resolve_metric_name(
        self,
        metric_name: str,
        subject_domain: str,
        filters: list,
    ) -> str | None:
        if self.semantic_runtime.is_known_metric(metric_name):
            return metric_name

        lowered = metric_name.lower()
        direct_matches: list[str] = []
        alias_matches: list[str] = []
        for candidate_name, metric in self.semantic_runtime.metric_catalog.items():
            semantic_column = str(metric.get("semantic_column", ""))
            if semantic_column.lower() == lowered:
                direct_matches.append(candidate_name)
                continue
            aliases = self.semantic_runtime.profile_field_aliases(subject_domain).get(candidate_name, [])
            if any(str(alias).lower() == lowered for alias in aliases):
                alias_matches.append(candidate_name)

        if direct_matches:
            candidates = direct_matches
        elif alias_matches:
            candidates = alias_matches
        else:
            return None

        selected = self._disambiguate_metric_candidates(candidates, filters)
        return selected

    def _disambiguate_metric_candidates(self, candidates: list[str], filters: list) -> str | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        act_type_value = None
        for item in filters:
            if item.field == "act_type" and isinstance(item.value, str):
                act_type_value = item.value
                break

        if act_type_value == "投入":
            input_candidates = [item for item in candidates if "input" in item]
            if input_candidates:
                candidates = input_candidates
        elif act_type_value == "产出":
            output_candidates = [item for item in candidates if "output" in item]
            if output_candidates:
                candidates = output_candidates

        non_derived_candidates = [
            item
            for item in candidates
            if self.semantic_runtime.metric_aggregate_function(item) != "DERIVED"
        ]
        if non_derived_candidates:
            candidates = non_derived_candidates

        return candidates[0]

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
