from __future__ import annotations

import re
from typing import Any

from backend.app.models.classification import QueryIntent
from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import SortItem
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryIntentParser:
    def __init__(
        self,
        domain_config: dict[str, Any],
        semantic_runtime: SemanticRuntime | None = None,
    ) -> None:
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime or SemanticRuntime(domain_config)
        self.metric_index = self._build_metric_index()
        self.entity_index = self._build_entity_index()

    def parse(self, question: str, session_state: SessionState | None = None) -> QueryIntent:
        normalized_question = question.strip().lower()
        matched_metrics = self._match_aliases(normalized_question, self.metric_index)
        matched_entities = self._match_aliases(normalized_question, self.entity_index)
        requested_dimensions = self.semantic_runtime.extract_dimensions(question)
        filters = self._extract_filters(question)
        time_context = self._extract_time_context(question)
        version_context = self.semantic_runtime.extract_version_context(question)
        matched_metrics = self.semantic_runtime.resolve_metrics(
            question=question,
            matched_metrics=matched_metrics,
            filters=filters,
        )
        requested_sort = self.semantic_runtime.extract_sort(question, matched_metrics)
        requested_limit = self.semantic_runtime.extract_limit(question)
        demand_shortcuts = self._extract_demand_shortcuts(question, matched_metrics)
        filters.extend(demand_shortcuts["filters"])
        requested_sort.extend(demand_shortcuts["sort"])
        if requested_limit is None:
            requested_limit = demand_shortcuts["limit"]
        analysis_mode = self.semantic_runtime.extract_analysis_mode(question)
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
            or bool(requested_sort)
            or requested_limit is not None
            or analysis_mode is not None
        )

        return QueryIntent(
            normalized_question=normalized_question,
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

    def _build_metric_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for metric in self.domain_config.get("metrics", []):
            index[metric["name"].lower()] = metric["name"]
            for alias in metric.get("aliases", []):
                index[alias.lower()] = metric["name"]
        return index

    def _build_entity_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for entity in self.domain_config.get("entities", []):
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

    def _extract_demand_shortcuts(self, question: str, matched_metrics: list[str]) -> dict:
        if "demand_qty" not in matched_metrics:
            return {"filters": [], "sort": [], "limit": None}

        filters: list[FilterItem] = []
        sort: list[SortItem] = []
        limit: int | None = None

        source_table = self._extract_demand_source_table(question)
        if source_table:
            filters.append(FilterItem(field="source_table", op="=", value=source_table))

        target_month = self._extract_compact_target_month(question)
        if target_month:
            filters.append(FilterItem(field="demand_month", op="=", value=target_month))

        latest_version_count = self._extract_latest_version_count(question)
        if latest_version_count is not None:
            filters.append(
                FilterItem(
                    field="PM_VERSION",
                    op="latest_n",
                    value={
                        "count": latest_version_count,
                        "source_table": source_table,
                    },
                )
            )

        if re.search(r"(?:最多|最大|最高|top\s*1|第一)", question, re.IGNORECASE):
            sort.append(SortItem(field="demand_qty", order="desc"))
            limit = 1

        return {
            "filters": self.semantic_runtime._deduplicate_filters(filters),
            "sort": sort,
            "limit": limit,
        }

    def _extract_demand_source_table(self, question: str) -> str | None:
        if re.search(r"(?<![A-Za-z0-9])p\s*(?:版|版本|demand|需求)", question, re.IGNORECASE):
            return "p_demand"
        if re.search(r"(?<![A-Za-z0-9])v\s*(?:版|版本|demand|需求)", question, re.IGNORECASE):
            return "v_demand"
        return None

    def _extract_compact_target_month(self, question: str) -> str | None:
        candidates = re.findall(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", question)
        if not candidates:
            return None
        return "".join(candidates[0])

    def _extract_latest_version_count(self, question: str) -> int | None:
        match = re.search(r"最新\s*(\d{1,2})\s*(?:个)?(?:版|版本)", question)
        if not match:
            return None
        return max(1, int(match.group(1)))
