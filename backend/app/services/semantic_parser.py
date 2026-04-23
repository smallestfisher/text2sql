from __future__ import annotations

from typing import Any

from backend.app.models.classification import SemanticParse
from backend.app.models.query_plan import FilterItem
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class SemanticParser:
    def __init__(
        self,
        semantic_layer: dict[str, Any],
        semantic_runtime: SemanticRuntime | None = None,
    ) -> None:
        self.semantic_layer = semantic_layer
        self.semantic_runtime = semantic_runtime or SemanticRuntime(semantic_layer)
        self.metric_index = self._build_metric_index()
        self.entity_index = self._build_entity_index()

    def parse(self, question: str, session_state: SessionState | None = None) -> SemanticParse:
        normalized_question = question.strip().lower()
        matched_metrics = self._match_aliases(normalized_question, self.metric_index)
        matched_entities = self._match_aliases(normalized_question, self.entity_index)
        requested_dimensions = self.semantic_runtime.extract_dimensions(question)
        filters = self._extract_filters(question)
        time_context = self._extract_time_context(question)
        version_context = self.semantic_runtime.extract_version_context(question)
        subject_domain = self.semantic_runtime.infer_domain(
            matched_metrics=matched_metrics,
            matched_entities=matched_entities,
            requested_dimensions=requested_dimensions,
            filters=filters,
            question=normalized_question,
            session_state=session_state,
        )
        has_follow_up_cue = any(
            cue.lower() in normalized_question for cue in self.semantic_runtime.follow_up_cues()
        )
        has_explicit_slots = bool(
            matched_metrics
            or requested_dimensions
            or filters
            or time_context.grain != "unknown"
            or version_context is not None
        )

        return SemanticParse(
            normalized_question=normalized_question,
            matched_metrics=matched_metrics,
            matched_entities=matched_entities,
            requested_dimensions=requested_dimensions,
            filters=filters,
            time_context=time_context,
            version_context=version_context,
            subject_domain=subject_domain,
            has_follow_up_cue=has_follow_up_cue,
            has_explicit_slots=has_explicit_slots,
        )

    def _build_metric_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for metric in self.semantic_layer.get("metrics", []):
            index[metric["name"].lower()] = metric["name"]
            for alias in metric.get("aliases", []):
                index[alias.lower()] = metric["name"]
        return index

    def _build_entity_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for entity in self.semantic_layer.get("entities", []):
            index[entity["name"].lower()] = entity["name"]
            for alias in entity.get("aliases", []):
                index[alias.lower()] = entity["name"]
        return index

    def _match_aliases(self, question: str, alias_index: dict[str, str]) -> list[str]:
        matched = {
            target_name
            for alias, target_name in alias_index.items()
            if alias and alias in question
        }
        return sorted(matched)

    def _extract_filters(self, question: str) -> list[FilterItem]:
        filters = self.semantic_runtime.extract_time_filters(question)
        filters.extend(self.semantic_runtime.extract_filters(question))

        return filters

    def _extract_time_context(self, question: str):
        return self.semantic_runtime.extract_time_context(question)
