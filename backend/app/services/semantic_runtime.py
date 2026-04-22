from __future__ import annotations

from collections import Counter
from collections import deque
import calendar
import re

from backend.app.models.classification import SemanticParse
from backend.app.models.query_plan import ContextDelta
from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import SortItem
from backend.app.models.query_plan import TimeContext
from backend.app.models.query_plan import TimeRange
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
        self.time_extractors = extractors.get("time", [])
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

    def classification_rules(self) -> list[dict]:
        classification = self.question_understanding.get("classification", {})
        return list(classification.get("rules", []))

    def metric_column(self, metric_name: str) -> str:
        metric = self.metric_catalog.get(metric_name, {})
        return metric.get("semantic_column", metric_name)

    def metric_aggregate_function(self, metric_name: str) -> str:
        metric = self.metric_catalog.get(metric_name, {})
        return str(metric.get("aggregate_function", "SUM")).upper()

    def is_known_metric(self, metric_name: str) -> bool:
        return metric_name in self.metric_catalog

    def is_known_domain(self, domain_name: str) -> bool:
        return domain_name in self.query_profiles or domain_name == "unknown"

    def domain_tables(self, domain_name: str) -> list[str]:
        for item in self.semantic_layer.get("domains", []):
            if item.get("name") == domain_name:
                return list(item.get("tables", []))
        return []

    def resolve_tables_for_plan(self, domain_name: str, metrics: list[str]) -> list[str]:
        profile = self.query_profile(domain_name)
        selection = profile.get("table_selection", {})
        prefer_metric_tables = bool(selection.get("prefer_metric_tables", True))
        fallback_to_domain_tables = bool(selection.get("fallback_to_domain_tables", True))

        if prefer_metric_tables and metrics:
            ordered_tables: list[str] = []
            for metric in metrics:
                for table in self.metric_tables(metric):
                    if table not in ordered_tables:
                        ordered_tables.append(table)
            if ordered_tables:
                return ordered_tables

        if fallback_to_domain_tables:
            return self.domain_tables(domain_name)
        return []

    def default_limit(self, domain_name: str, fallback: int = 200) -> int:
        profile = self.query_profile(domain_name)
        return int(profile.get("default_limit", fallback))

    def max_limit(self, domain_name: str, fallback: int = 200) -> int:
        profile = self.query_profile(domain_name)
        return int(profile.get("max_limit", fallback))

    def clamp_limit(self, domain_name: str, limit: int | None, fallback: int = 200) -> int:
        default_limit = self.default_limit(domain_name, fallback=fallback)
        max_limit = self.max_limit(domain_name, fallback=default_limit)
        if limit is None or limit <= 0:
            return default_limit
        return min(limit, max_limit)

    def sanitize_query_plan(
        self,
        query_plan: QueryPlan,
        fallback_semantic_views: list[str] | None = None,
        default_limit: int = 200,
    ) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        compiled.entities = [
            entity
            for entity in self._unique_strings(compiled.entities)
            if entity in self.entity_catalog
        ]
        compiled.metrics = [
            metric
            for metric in self._unique_strings(compiled.metrics)
            if self.is_known_metric(metric)
        ]
        compiled.semantic_views = self._sanitize_semantic_views(
            domain_name=compiled.subject_domain,
            metrics=compiled.metrics,
            dimensions=compiled.dimensions,
            filters=compiled.filters,
            sort_fields=[item.field for item in compiled.sort],
            version_field=compiled.version_context.field if compiled.version_context else None,
            candidate_views=compiled.semantic_views,
            fallback_semantic_views=fallback_semantic_views,
        )
        compiled.tables = self._sanitize_tables(
            domain_name=compiled.subject_domain,
            metrics=compiled.metrics,
            candidate_tables=compiled.tables,
        )
        compiled.version_context = self._sanitize_version_context(
            domain_name=compiled.subject_domain,
            semantic_views=compiled.semantic_views,
            version_context=compiled.version_context,
        )
        compiled.limit = self.clamp_limit(
            compiled.subject_domain,
            compiled.limit,
            fallback=default_limit,
        )
        compiled = self.apply_domain_constraints(compiled)
        allowed_fields = self.allowed_fields_for_plan(compiled)
        compiled.dimensions = self._sanitize_dimensions(compiled.dimensions, allowed_fields)
        compiled.filters = self._sanitize_filters(compiled.filters, allowed_fields)
        compiled.sort = self._sanitize_sort(compiled.sort, allowed_fields)
        compiled.join_path = self.resolve_join_path(compiled.tables)
        return compiled

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

    def semantic_views_support_field(self, view_names: list[str], field_name: str) -> bool:
        return any(field_name in self.semantic_view_fields(view_name) for view_name in view_names)

    def allowed_fields_for_plan(self, query_plan: QueryPlan) -> set[str]:
        allowed_fields: set[str] = set()
        for view_name in query_plan.semantic_views:
            allowed_fields.update(self.semantic_view_fields(view_name))
        profile_version_field = self.query_profile(query_plan.subject_domain).get("version_field")
        if (
            query_plan.version_context
            and query_plan.version_context.field
            and (
                query_plan.version_context.field == profile_version_field
                or self.semantic_views_support_field(query_plan.semantic_views, query_plan.version_context.field)
            )
        ):
            allowed_fields.add(query_plan.version_context.field)
        return allowed_fields

    def llm_plan_is_acceptable(self, candidate: QueryPlan, base_plan: QueryPlan) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        if candidate.subject_domain == "unknown" and base_plan.subject_domain != "unknown":
            reasons.append("llm plan lost known subject domain")

        if base_plan.metrics and not candidate.metrics:
            reasons.append("llm plan dropped all metrics")

        if (
            base_plan.version_context
            and base_plan.version_context.value
            and candidate.version_context is None
        ):
            reasons.append("llm plan dropped version context from base plan")

        allowed_views = set(self.semantic_views_for_domain(candidate.subject_domain))
        if candidate.semantic_views and allowed_views:
            unsupported_views = [item for item in candidate.semantic_views if item not in allowed_views]
            if unsupported_views:
                reasons.append("llm plan selected semantic views outside domain defaults")

        allowed_fields = self.allowed_fields_for_plan(candidate)
        if allowed_fields:
            bad_dimensions = [item for item in candidate.dimensions if item not in allowed_fields]
            if bad_dimensions:
                reasons.append("llm plan dimensions are outside allowed fields")
            bad_filters = [item.field for item in candidate.filters if item.field not in allowed_fields]
            if bad_filters:
                reasons.append("llm plan filters are outside allowed fields")

        if candidate.subject_domain != "unknown" and not candidate.semantic_views and not candidate.tables:
            reasons.append("llm plan does not provide semantic views or tables")

        if candidate.limit > self.max_limit(candidate.subject_domain, fallback=base_plan.limit):
            reasons.append("llm plan limit exceeds configured maximum")

        profile_version_field = self.query_profile(candidate.subject_domain).get("version_field")
        if (
            candidate.version_context
            and candidate.version_context.field
            and not (
                candidate.version_context.field == profile_version_field
                or self.semantic_views_support_field(candidate.semantic_views, candidate.version_context.field)
            )
        ):
            reasons.append("llm plan version field is not supported by domain or semantic views")

        return not reasons, reasons

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
        candidate_views: list[str] | None = None,
    ) -> list[str]:
        domain_views = self.semantic_views_for_domain(domain_name)
        candidates = [
            view_name
            for view_name in self._unique_strings(candidate_views or [])
            if self.is_known_view(view_name)
            and (not domain_views or view_name in domain_views)
        ]
        if not candidates:
            candidates = domain_views
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

    def extract_time_filters(self, question: str) -> list[FilterItem]:
        filters: list[FilterItem] = []
        for rule in self.time_extractors:
            extracted = self._extract_time_rule(question, rule)
            if extracted is not None and extracted["filter"] is not None:
                filters.append(extracted["filter"])
        return self._deduplicate_filters(filters)

    def extract_time_context(self, question: str) -> TimeContext:
        for rule in self.time_extractors:
            extracted = self._extract_time_rule(question, rule)
            if extracted is None or extracted["context"] is None:
                continue
            return extracted["context"]
        return TimeContext(grain="unknown", range=TimeRange())

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
            compiled.reason_code = rule.get("reason_code")
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

    def _sanitize_semantic_views(
        self,
        domain_name: str,
        metrics: list[str],
        dimensions: list[str],
        filters: list[FilterItem],
        sort_fields: list[str],
        version_field: str | None,
        candidate_views: list[str] | None,
        fallback_semantic_views: list[str] | None,
    ) -> list[str]:
        ranked = self.rank_semantic_views(
            domain_name=domain_name,
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            sort_fields=sort_fields,
            version_field=version_field,
            candidate_views=candidate_views,
        )
        if ranked:
            return ranked
        if fallback_semantic_views:
            return self.rank_semantic_views(
                domain_name=domain_name,
                metrics=metrics,
                dimensions=dimensions,
                filters=filters,
                sort_fields=sort_fields,
                version_field=version_field,
                candidate_views=fallback_semantic_views,
            )
        return []

    def _sanitize_tables(
        self,
        domain_name: str,
        metrics: list[str],
        candidate_tables: list[str],
    ) -> list[str]:
        allowed_domain_tables = set(self.domain_tables(domain_name))
        filtered_tables = [
            table
            for table in self._unique_strings(candidate_tables)
            if self.is_known_table(table)
            and (not allowed_domain_tables or table in allowed_domain_tables)
        ]
        if filtered_tables:
            return filtered_tables
        derived_tables = self.resolve_tables_for_plan(domain_name, metrics)
        return [
            table
            for table in self._unique_strings(derived_tables)
            if self.is_known_table(table)
            and (not allowed_domain_tables or table in allowed_domain_tables)
        ]

    def _sanitize_version_context(
        self,
        domain_name: str,
        semantic_views: list[str],
        version_context: VersionContext | None,
    ) -> VersionContext | None:
        if version_context is None or not version_context.value:
            return None
        profile_version_field = self.query_profile(domain_name).get("version_field")
        version_field = version_context.field or profile_version_field
        if not version_field:
            return None
        if (
            version_field != profile_version_field
            and not self.semantic_views_support_field(semantic_views, version_field)
        ):
            return None
        return VersionContext(field=version_field, value=version_context.value)

    def _sanitize_dimensions(self, dimensions: list[str], allowed_fields: set[str]) -> list[str]:
        return [
            field
            for field in self._unique_strings(dimensions)
            if field and (not allowed_fields or field in allowed_fields)
        ]

    def _sanitize_filters(
        self,
        filters: list[FilterItem],
        allowed_fields: set[str],
    ) -> list[FilterItem]:
        deduplicated: list[FilterItem] = []
        seen: set[str] = set()
        for item in filters:
            if allowed_fields and item.field not in allowed_fields:
                continue
            key = f"{item.field}:{item.op}:{repr(item.value)}"
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)
        return deduplicated

    def _sanitize_sort(self, sort_items: list[SortItem], allowed_fields: set[str]) -> list[SortItem]:
        deduplicated: list[SortItem] = []
        seen: set[str] = set()
        for item in sort_items:
            if allowed_fields and item.field not in allowed_fields:
                continue
            key = f"{item.field}:{item.order}"
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)
        return deduplicated

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

    def _extract_time_rule(self, question: str, rule: dict) -> dict | None:
        pattern = rule.get("pattern", "")
        if not pattern:
            return None

        source = self._prepare_text(question, rule)
        flags = 0
        if "ignorecase" in rule.get("flags", []):
            flags |= re.IGNORECASE
        match = re.search(pattern, source, flags)
        if not match:
            return None

        rule_type = rule.get("type")
        field = rule.get("field")
        if rule_type == "calendar_day":
            year, month, day = match.groups()
            value = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            return {
                "filter": FilterItem(field=field, op="=", value=value) if field else None,
                "context": TimeContext(grain="day", range=TimeRange(start=value, end=value)),
            }

        if rule_type == "iso_day":
            value = match.group(1)
            return {
                "filter": FilterItem(field=field, op="=", value=value) if field else None,
                "context": TimeContext(grain="day", range=TimeRange(start=value, end=value)),
            }

        if rule_type == "calendar_month":
            year, month = match.groups()
            month_end = calendar.monthrange(int(year), int(month))[1]
            start = f"{int(year):04d}-{int(month):02d}-01"
            end = f"{int(year):04d}-{int(month):02d}-{month_end:02d}"
            return {
                "filter": FilterItem(field=field, op="between", value=[start, end]) if field else None,
                "context": TimeContext(grain="month", range=TimeRange(start=start, end=end)),
            }

        return None

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
        if (
            version_field != profile.get("version_field")
            and not self.semantic_views_support_field(compiled.semantic_views, version_field)
        ):
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
