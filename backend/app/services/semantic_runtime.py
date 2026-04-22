from __future__ import annotations

from collections import deque
from collections import Counter
import re

from backend.app.models.classification import SemanticParse
from backend.app.models.query_plan import ContextDelta
from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import SortItem
from backend.app.models.query_plan import VersionContext
from backend.app.models.session_state import SessionState


class SemanticRuntime:
    def __init__(self, semantic_layer: dict) -> None:
        self.semantic_layer = semantic_layer
        self.metric_catalog = {
            item["name"]: item for item in semantic_layer.get("metrics", [])
        }
        self.entity_catalog = {
            item["name"]: item for item in semantic_layer.get("entities", [])
        }
        self.view_catalog = {
            item["name"]: item for item in semantic_layer.get("semantic_views", [])
        }
        self.query_profiles = semantic_layer.get("query_profiles", {})
        self.question_understanding = semantic_layer.get("question_understanding", {})
        self.domain_inference = semantic_layer.get("domain_inference", {})
        extractors = semantic_layer.get("extractors", {})
        self.filter_extractors = extractors.get("filters", [])
        self.version_extractors = extractors.get("version", [])
        self.graph_nodes = set(semantic_layer.get("semantic_graph", {}).get("nodes", []))
        self.graph_edges = semantic_layer.get("semantic_graph", {}).get("edges", [])

    def invalid_patterns(self) -> set[str]:
        return set(self.question_understanding.get("invalid_exact_patterns", []))

    def follow_up_cues(self) -> list[str]:
        return list(self.question_understanding.get("follow_up_cues", []))

    def context_filter_groups(self) -> dict[str, list[str]]:
        context_management = self.question_understanding.get("context_management", {})
        return context_management.get("replace_filter_groups", {})

    def clarification_message(self, key: str, default: str) -> str:
        messages = self.question_understanding.get("clarification_questions", {})
        return messages.get(key, default)

    def metric_column(self, metric_name: str) -> str:
        metric = self.metric_catalog.get(metric_name, {})
        return metric.get("semantic_column", metric_name)

    def metric_tables(self, metric_name: str) -> list[str]:
        metric = self.metric_catalog.get(metric_name)
        if metric is None:
            return []
        return [item["table"] for item in metric.get("definitions", [])]

    def resolve_field(self, source_name: str | None, logical_field: str) -> str:
        if not source_name:
            return logical_field
        source = self.view_catalog.get(source_name, {})
        field_aliases = source.get("field_aliases", {})
        return field_aliases.get(logical_field, logical_field)

    def semantic_view_fields(self, view_name: str) -> list[str]:
        view = self.view_catalog.get(view_name, {})
        return list(view.get("output_fields", []))

    def allowed_fields_for_plan(self, query_plan: QueryPlan) -> set[str]:
        allowed_fields: set[str] = set()
        for view_name in query_plan.semantic_views:
            allowed_fields.update(self.semantic_view_fields(view_name))
        if query_plan.version_context and query_plan.version_context.field:
            allowed_fields.add(query_plan.version_context.field)
        return allowed_fields

    def semantic_views_for_domain(self, domain_name: str) -> list[str]:
        profile = self.query_profiles.get(domain_name, {})
        return profile.get("default_semantic_views", [])

    def query_profile(self, domain_name: str) -> dict:
        return self.query_profiles.get(domain_name, {})

    def rank_semantic_views(
        self,
        domain_name: str,
        metrics: list[str] | None = None,
        dimensions: list[str] | None = None,
        filters: list[FilterItem] | None = None,
        sort_fields: list[str] | None = None,
        version_field: str | None = None,
    ) -> list[str]:
        candidates = self.semantic_views_for_domain(domain_name)
        if len(candidates) <= 1:
            return candidates

        requested_fields = set(dimensions or [])
        requested_fields.update(item.field for item in (filters or []))
        requested_fields.update(sort_fields or [])
        if version_field:
            requested_fields.add(version_field)

        requested_metric_fields = {self.metric_column(metric) for metric in (metrics or [])}

        scored = []
        for index, view_name in enumerate(candidates):
            output_fields = set(self.semantic_view_fields(view_name))
            score = 0
            score += 5 * len(output_fields.intersection(requested_metric_fields))
            score += 2 * len(output_fields.intersection(requested_fields))
            scored.append((score, -index, view_name))

        scored.sort(reverse=True)
        return [item[2] for item in scored]

    def time_filter_fields(self, domain_name: str) -> list[str]:
        return list(self.query_profile(domain_name).get("time_filter_fields", []))

    def warn_if_missing_time_filter(self, domain_name: str) -> bool:
        return bool(self.query_profile(domain_name).get("warn_if_missing_time_filter", False))

    def build_context_delta(self, semantic_parse: SemanticParse) -> ContextDelta:
        remove_filters: list[str] = []
        incoming_fields = {item.field for item in semantic_parse.filters}
        for fields in self.context_filter_groups().values():
            group = set(fields)
            if group.intersection(incoming_fields):
                remove_filters.extend(sorted(group))

        if semantic_parse.version_context and semantic_parse.version_context.field:
            version_group = self.context_filter_groups().get("version", [])
            remove_filters.extend(version_group or [semantic_parse.version_context.field])

        return ContextDelta(
            add_filters=semantic_parse.filters,
            remove_filters=self._unique_strings(remove_filters),
            replace_metrics=semantic_parse.matched_metrics,
            replace_dimensions=[],
            replace_time_context=semantic_parse.time_context,
        )

    def session_semantic_diff(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
    ) -> dict:
        if session_state is None:
            return {
                "has_session": False,
                "domain_changed": semantic_parse.subject_domain != "unknown",
                "new_metrics": semantic_parse.matched_metrics,
                "new_entities": semantic_parse.matched_entities,
                "new_filter_fields": [item.field for item in semantic_parse.filters],
            }

        current_filter_fields = {item.field for item in semantic_parse.filters}
        previous_filter_fields = {item.field for item in session_state.filters}
        return {
            "has_session": True,
            "domain_changed": semantic_parse.subject_domain != session_state.subject_domain,
            "new_metrics": [
                item for item in semantic_parse.matched_metrics if item not in session_state.metrics
            ],
            "reused_metrics": [
                item for item in semantic_parse.matched_metrics if item in session_state.metrics
            ],
            "new_entities": [
                item for item in semantic_parse.matched_entities if item not in session_state.entities
            ],
            "reused_entities": [
                item for item in semantic_parse.matched_entities if item in session_state.entities
            ],
            "new_filter_fields": sorted(current_filter_fields - previous_filter_fields),
            "reused_filter_fields": sorted(current_filter_fields.intersection(previous_filter_fields)),
            "has_follow_up_cue": semantic_parse.has_follow_up_cue,
            "has_explicit_slots": semantic_parse.has_explicit_slots,
            "time_grain_changed": (
                session_state.time_context is not None
                and semantic_parse.time_context.grain != "unknown"
                and semantic_parse.time_context.grain != session_state.time_context.grain
            ),
            "version_changed": bool(
                semantic_parse.version_context is not None
                and semantic_parse.version_context.value
                != (session_state.version_context.value if session_state.version_context else None)
            ),
        }

    def infer_domain(
        self,
        matched_metrics: list[str],
        matched_entities: list[str],
        filters: list[FilterItem] | None = None,
        session_state: SessionState | None = None,
    ) -> str:
        metric_to_domain = self.domain_inference.get("metric_to_domain", {})
        domains = [metric_to_domain.get(metric) for metric in matched_metrics if metric_to_domain.get(metric)]
        if domains:
            return Counter(domains).most_common(1)[0][0]

        filter_fields = {item.field for item in filters or []}
        entity_set = set(matched_entities)
        hint_counter: Counter[str] = Counter()
        for hint in self.domain_inference.get("hints", []):
            domain = hint.get("domain")
            if not domain:
                continue
            hint_entities = set(hint.get("entities", []))
            hint_filter_fields = set(hint.get("filter_fields", []))
            if hint_entities.intersection(entity_set) or hint_filter_fields.intersection(filter_fields):
                hint_counter[domain] += int(hint.get("weight", 1))

        if hint_counter:
            return hint_counter.most_common(1)[0][0]

        if session_state and self.domain_inference.get("fallback_to_session", True):
            return session_state.subject_domain

        return "unknown"

    def suggest_dimensions(
        self,
        subject_domain: str,
        matched_entities: list[str],
        filter_fields: set[str],
        time_grain: str,
    ) -> list[str]:
        profile = self.query_profiles.get(subject_domain, {})
        preferences = profile.get("dimension_preferences", [])
        dimensions: list[str] = []
        entities = set(matched_entities)

        for rule in preferences:
            required_entities = set(rule.get("entities", []))
            excluded_filter_fields = set(rule.get("exclude_filter_fields", []))
            rule_time_grain = rule.get("time_grain")

            if required_entities and not required_entities.issubset(entities):
                continue
            if rule_time_grain and rule_time_grain != time_grain:
                continue
            if excluded_filter_fields.intersection(filter_fields):
                continue

            for dimension in rule.get("add_dimensions", []):
                if dimension not in dimensions:
                    dimensions.append(dimension)

        return dimensions

    def extract_filters(self, question: str) -> list[FilterItem]:
        filters: list[FilterItem] = []
        for rule in self.filter_extractors:
            extracted = self._extract_filter(question, rule)
            if extracted is not None:
                filters.append(extracted)
        return self._deduplicate_filters(filters)

    def extract_version_context(self, question: str) -> VersionContext | None:
        for rule in self.version_extractors:
            extracted = self._extract_regex_value(question, rule)
            if extracted is None:
                continue
            return VersionContext(field=rule.get("field"), value=extracted)
        return None

    def apply_domain_constraints(self, query_plan: QueryPlan) -> QueryPlan:
        profile = self.query_profiles.get(query_plan.subject_domain, {})
        compiled = query_plan.model_copy(deep=True)

        if not compiled.semantic_views:
            compiled.semantic_views = profile.get("default_semantic_views", [])

        compiled = self._inject_version_filter(compiled, profile)
        compiled = self._inject_default_sort(compiled, profile)

        drop_dimensions = set(profile.get("drop_dimensions", []))
        if drop_dimensions:
            compiled.dimensions = [
                item for item in compiled.dimensions if item not in drop_dimensions
            ]

        drop_filters = set(profile.get("drop_filters", []))
        if drop_filters:
            compiled.filters = [
                item for item in compiled.filters if item.field not in drop_filters
            ]

        entities = set(compiled.entities)
        filter_fields = {item.field for item in compiled.filters}
        for rule in profile.get("clarification_rules", []):
            required_entities = set(rule.get("entities", []))
            excluded_entities = set(rule.get("exclude_entities", []))
            excluded_filter_fields = set(rule.get("exclude_filter_fields", []))

            if required_entities and not required_entities.issubset(entities):
                continue
            if excluded_entities.intersection(entities):
                continue
            if excluded_filter_fields.intersection(filter_fields):
                continue

            compiled.need_clarification = True
            compiled.question_type = rule.get("question_type", "clarification_needed")
            compiled.reason = rule.get("reason")
            compiled.clarification_question = rule.get("clarification_question")
            break

        return compiled

    def resolve_join_path(self, tables: list[str]) -> list[str]:
        if len(tables) < 2:
            return []
        resolved: list[str] = []
        for index in range(len(tables) - 1):
            path = self._find_edge_path(tables[index], tables[index + 1])
            if path:
                resolved.extend(path)
        return resolved

    def is_known_table(self, table: str) -> bool:
        return table in self.graph_nodes

    def is_known_view(self, view: str) -> bool:
        return view in self.view_catalog

    def _find_edge_path(self, start: str, target: str) -> list[str]:
        if start == target:
            return []

        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in self.graph_edges:
            adjacency.setdefault(edge["from"], []).append((edge["to"], edge["on"]))
            adjacency.setdefault(edge["to"], []).append((edge["from"], edge["on"]))

        queue = deque([(start, [])])
        visited = {start}
        while queue:
            node, path = queue.popleft()
            for neighbor, on in adjacency.get(node, []):
                if neighbor in visited:
                    continue
                next_path = path + [on]
                if neighbor == target:
                    return next_path
                visited.add(neighbor)
                queue.append((neighbor, next_path))
        return []

    def _extract_filter(self, question: str, rule: dict) -> FilterItem | None:
        rule_type = rule.get("type")
        if rule_type == "regex":
            value = self._extract_regex_value(question, rule)
            if value is None:
                return None
            return FilterItem(field=rule["field"], op=rule.get("op", "="), value=value)

        if rule_type == "keyword_enum":
            source = self._prepare_text(question, rule)
            for candidate in rule.get("candidates", []):
                normalized_candidate = candidate.upper() if "uppercase" in rule.get("flags", []) else candidate
                if self._contains_candidate(source, normalized_candidate):
                    return FilterItem(
                        field=rule["field"],
                        op=rule.get("op", "="),
                        value=candidate,
                    )
        return None

    def _extract_regex_value(self, question: str, rule: dict) -> str | None:
        flags = 0
        if "ignorecase" in rule.get("flags", []):
            flags |= re.IGNORECASE
        source = self._prepare_text(question, rule)
        match = re.search(rule.get("pattern", ""), source, flags)
        if not match:
            return None
        return match.group(1)

    def _prepare_text(self, question: str, rule: dict) -> str:
        if "uppercase" in rule.get("flags", []):
            return question.upper()
        return question

    def _contains_candidate(self, source: str, candidate: str) -> bool:
        if re.fullmatch(r"[A-Z0-9_]+", candidate):
            return re.search(rf"\b{re.escape(candidate)}\b", source) is not None
        return candidate in source

    def _deduplicate_filters(self, filters: list[FilterItem]) -> list[FilterItem]:
        deduplicated: list[FilterItem] = []
        seen: set[str] = set()
        for item in filters:
            key = f"{item.field}:{item.op}:{repr(item.value)}"
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)
        return deduplicated

    def _inject_version_filter(self, query_plan: QueryPlan, profile: dict) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        if compiled.version_context is None or not compiled.version_context.value:
            return compiled

        version_field = compiled.version_context.field or profile.get("version_field")
        if not version_field:
            return compiled

        existing = {
            f"{item.field}:{item.op}:{repr(item.value)}"
            for item in compiled.filters
        }
        version_filter = FilterItem(field=version_field, op="=", value=compiled.version_context.value)
        version_key = f"{version_filter.field}:{version_filter.op}:{repr(version_filter.value)}"
        if version_key not in existing:
            compiled.filters = [
                item for item in compiled.filters if item.field != version_filter.field
            ] + [version_filter]
        return compiled

    def _inject_default_sort(self, query_plan: QueryPlan, profile: dict) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        if compiled.sort:
            return compiled
        default_sort = profile.get("default_sort", [])
        if not default_sort:
            return compiled
        compiled.sort = [SortItem(**item) for item in default_sort if isinstance(item, dict)]
        return compiled

    def _unique_strings(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
