from __future__ import annotations

from collections import Counter
from collections import deque
import calendar
import json
import re
from datetime import date

from backend.app.config import TABLES_METADATA_PATH
from backend.app.models.classification import QueryIntent
from backend.app.models.query_plan import ContextDelta
from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import SortItem
from backend.app.models.query_plan import TimeContext
from backend.app.models.query_plan import TimeRange
from backend.app.models.query_plan import VersionContext
from backend.app.models.session_state import SessionState


class SemanticRuntime:
    def __init__(self, domain_config: dict) -> None:
        self.domain_config = domain_config
        self.metric_catalog = {
            item["name"]: item for item in domain_config.get("metrics", [])
        }
        self.entity_catalog = {
            item["name"]: item for item in domain_config.get("entities", [])
        }
        self.query_profiles = domain_config.get("query_profiles", {})
        self.question_understanding = domain_config.get("question_understanding", {})
        self.domain_inference = domain_config.get("domain_inference", {})
        extractors = domain_config.get("extractors", {})
        self.time_extractors = extractors.get("time", [])
        self.filter_extractors = extractors.get("filters", [])
        self.dimension_extractors = extractors.get("dimensions", [])
        self.version_extractors = extractors.get("version", [])
        self.analysis_extractors = extractors.get("analysis", [])
        self.sort_extractors = extractors.get("sort", [])
        self.limit_extractors = extractors.get("limit", [])
        self.graph_nodes = set(domain_config.get("semantic_graph", {}).get("nodes", []))
        self.graph_edges = domain_config.get("semantic_graph", {}).get("edges", [])
        self.tables_metadata = self._load_tables_metadata()
        self.table_field_catalog = {
            table_name: self._extract_table_fields(payload)
            for table_name, payload in self.tables_metadata.items()
            if isinstance(payload, dict)
        }

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

    def metric_resolution_rules(self) -> list[dict]:
        return list(self.question_understanding.get("metric_resolution_rules", []))

    def resolve_metrics(
        self,
        question: str,
        matched_metrics: list[str],
        filters: list[FilterItem],
    ) -> list[str]:
        metrics = [
            metric
            for metric in self._unique_strings(matched_metrics)
            if self.is_known_metric(metric)
        ]
        if metrics:
            return metrics

        normalized_question = question.strip().lower()
        for rule in self.metric_resolution_rules():
            if not self._metric_resolution_rule_matches(normalized_question, filters, rule):
                continue
            for metric in rule.get("metrics", []):
                if isinstance(metric, str) and self.is_known_metric(metric) and metric not in metrics:
                    metrics.append(metric)
        return metrics

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
        for item in self.domain_config.get("domains", []):
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
        compiled.tables = self._sanitize_tables(
            domain_name=compiled.subject_domain,
            metrics=compiled.metrics,
            candidate_tables=compiled.tables,
        )
        compiled.version_context = self._sanitize_version_context(
            domain_name=compiled.subject_domain,
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

    def profile_allowed_fields(self, domain_name: str) -> list[str]:
        profile = self.query_profile(domain_name)
        return [str(item) for item in profile.get("allowed_fields", []) if item]

    def profile_field_aliases(self, domain_name: str) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        raw_aliases = self.query_profile(domain_name).get("field_aliases", {})
        if not isinstance(raw_aliases, dict):
            return aliases
        for field_name, targets in raw_aliases.items():
            if not field_name:
                continue
            if isinstance(targets, list):
                values = [str(item) for item in targets if item]
            elif targets:
                values = [str(targets)]
            else:
                values = []
            aliases[str(field_name)] = values
        return aliases

    def table_fields(self, table_name: str) -> list[str]:
        return list(self.table_field_catalog.get(table_name, []))

    def resolve_field_candidates(
        self,
        domain_name: str,
        table_names: list[str],
        logical_field: str,
    ) -> set[str]:
        candidates = {logical_field}
        aliases = self.profile_field_aliases(domain_name).get(logical_field, [])
        candidates.update(alias for alias in aliases if alias)

        lowered_field = logical_field.lower()
        for table_name in table_names:
            for column_name in self.table_fields(table_name):
                if column_name.lower() == lowered_field:
                    candidates.add(column_name)

        for metric_name, metric in self.metric_catalog.items():
            if str(metric.get("semantic_column", "")) != logical_field:
                continue
            candidates.update(self.metric_expression_columns(metric_name, table_names=table_names))
            break
        return {item for item in candidates if item}

    def is_dynamic_version_context(self, version_context: VersionContext | None) -> bool:
        return bool(
            version_context
            and isinstance(version_context.value, str)
            and version_context.value.startswith("LATEST_N:")
        )

    def allowed_fields_for_plan(self, query_plan: QueryPlan) -> set[str]:
        allowed_fields = set(self.profile_allowed_fields(query_plan.subject_domain))
        for table_name in query_plan.tables:
            allowed_fields.update(self.table_fields(table_name))
        for metric_name in query_plan.metrics:
            allowed_fields.add(self.metric_column(metric_name))
            allowed_fields.update(self.metric_expression_columns(metric_name, table_names=query_plan.tables))
        profile_version_field = self.query_profile(query_plan.subject_domain).get("version_field")
        if query_plan.version_context and query_plan.version_context.field:
            if not profile_version_field or query_plan.version_context.field == profile_version_field:
                allowed_fields.add(query_plan.version_context.field)
        return allowed_fields

    def metric_expression_columns(self, metric_name: str, table_names: list[str] | None = None) -> set[str]:
        metric = self.metric_catalog.get(metric_name, {})
        allowed_tables = set(table_names or [])
        columns: set[str] = set()
        for definition in metric.get("definitions", []):
            table_name = definition.get("table")
            if allowed_tables and table_name not in allowed_tables:
                continue
            expression = str(definition.get("expression", ""))
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression):
                upper_token = token.upper()
                if upper_token in {"SUM", "COUNT", "AVG", "MIN", "MAX", "NORMALIZED_FROM_HORIZONTAL_MONTH_COLUMNS"}:
                    continue
                columns.add(token)
        return columns

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

        allowed_fields = self.allowed_fields_for_plan(candidate)
        if allowed_fields:
            bad_dimensions = [item for item in candidate.dimensions if item not in allowed_fields]
            if bad_dimensions:
                reasons.append("llm plan dimensions are outside allowed fields")
            bad_filters = [item.field for item in candidate.filters if item.field not in allowed_fields]
            if bad_filters:
                reasons.append("llm plan filters are outside allowed fields")

        if candidate.subject_domain != "unknown" and not candidate.tables:
            reasons.append("llm plan does not provide tables")

        if candidate.limit > self.max_limit(candidate.subject_domain, fallback=base_plan.limit):
            reasons.append("llm plan limit exceeds configured maximum")

        profile_version_field = self.query_profile(candidate.subject_domain).get("version_field")
        if (
            candidate.version_context
            and candidate.version_context.field
            and profile_version_field
            and candidate.version_context.field != profile_version_field
        ):
            reasons.append("llm plan version field is not supported by domain configuration")

        return not reasons, reasons

    def query_profile(self, domain_name: str) -> dict:
        return self.query_profiles.get(domain_name, {})

    def time_filter_fields(self, domain_name: str) -> list[str]:
        return list(self.query_profile(domain_name).get("time_filter_fields", []))

    def warn_if_missing_time_filter(self, domain_name: str) -> bool:
        return bool(self.query_profile(domain_name).get("warn_if_missing_time_filter", False))

    def build_context_delta(self, query_intent: QueryIntent) -> ContextDelta:
        remove_filters: list[str] = []
        incoming_fields = {item.field for item in query_intent.filters}
        for fields in self.context_filter_groups().values():
            group = set(fields)
            if group.intersection(incoming_fields):
                remove_filters.extend(sorted(group))

        if query_intent.version_context and query_intent.version_context.field:
            version_group = self.context_filter_groups().get("version", [])
            remove_filters.extend(version_group or [query_intent.version_context.field])

        return ContextDelta(
            add_filters=query_intent.filters,
            remove_filters=self._unique_strings(remove_filters),
            replace_entities=query_intent.matched_entities,
            replace_metrics=query_intent.matched_metrics,
            replace_dimensions=query_intent.requested_dimensions,
            replace_sort=query_intent.requested_sort,
            replace_time_context=query_intent.time_context,
            replace_version_context=query_intent.version_context,
            replace_limit=query_intent.requested_limit,
            replace_analysis_mode=query_intent.analysis_mode,
        )

    def session_semantic_diff(
        self,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict:
        current_filter_fields = {item.field for item in query_intent.filters}
        parsed_domain_known = query_intent.subject_domain != "unknown"
        current_has_dimensions = bool(query_intent.requested_dimensions)
        current_has_time = query_intent.time_context.grain != "unknown"
        current_has_version = bool(
            query_intent.version_context is not None and query_intent.version_context.value
        )
        current_has_sort = bool(query_intent.requested_sort)
        current_has_limit = query_intent.requested_limit is not None
        if session_state is None:
            return {
                "has_session": False,
                "parsed_domain_known": parsed_domain_known,
                "domain_changed": parsed_domain_known,
                "new_metrics": query_intent.matched_metrics,
                "new_entities": query_intent.matched_entities,
                "requested_dimensions": query_intent.requested_dimensions,
                "new_filter_fields": sorted(current_filter_fields),
                "reused_filter_fields": [],
                "only_updates_filters": False,
                "only_updates_dimensions": current_has_dimensions,
                "only_updates_time": False,
                "only_updates_version": False,
                "only_updates_sort": False,
                "only_updates_limit": False,
                "has_independent_target": bool(query_intent.matched_metrics or parsed_domain_known),
                "can_execute_without_context": bool(
                    query_intent.matched_metrics
                    and (parsed_domain_known or current_has_time or current_filter_fields)
                ),
                "is_short_followup_fragment": False,
                "explicit_time_or_version_slot": current_has_time or current_has_version,
                "metric_overlap_ratio": 0.0,
                "entity_overlap_ratio": 0.0,
                "filter_overlap_ratio": 0.0,
                "metrics_missing_but_context_resolvable": False,
                "introduces_new_topic_signal": parsed_domain_known and bool(query_intent.matched_metrics),
            }

        previous_filter_fields = {item.field for item in session_state.filters}
        previous_metrics = set(session_state.metrics)
        previous_entities = set(session_state.entities)
        current_metrics = set(query_intent.matched_metrics)
        current_entities = set(query_intent.matched_entities)
        reused_metric_count = len(current_metrics.intersection(previous_metrics))
        reused_entity_count = len(current_entities.intersection(previous_entities))
        reused_filter_count = len(current_filter_fields.intersection(previous_filter_fields))
        previous_has_time = bool(session_state.time_context and session_state.time_context.grain != "unknown")
        previous_has_version = bool(
            session_state.version_context is not None and session_state.version_context.value
        )
        metric_changed = bool(current_metrics - previous_metrics)
        only_updates_filters = bool(current_filter_fields) and not current_metrics and not current_has_dimensions and not current_has_time and not current_has_version and not current_has_sort and not current_has_limit
        only_updates_dimensions = current_has_dimensions and not current_metrics and not current_filter_fields and not current_has_time and not current_has_version and not current_has_sort and not current_has_limit
        only_updates_time = current_has_time and not current_metrics and not current_entities and not current_filter_fields and not current_has_version and not current_has_sort and not current_has_limit
        only_updates_version = current_has_version and not current_metrics and not current_entities and not current_filter_fields and not current_has_time and not current_has_sort and not current_has_limit
        only_updates_sort = current_has_sort and not metric_changed and not current_entities and not current_filter_fields and not current_has_time and not current_has_version and not current_has_dimensions
        only_updates_limit = current_has_limit and not metric_changed and not current_entities and not current_filter_fields and not current_has_time and not current_has_version and not current_has_dimensions
        can_execute_without_context = bool(
            query_intent.matched_metrics
            and (parsed_domain_known or current_has_time or current_filter_fields or current_entities)
            and not (only_updates_sort or only_updates_limit)
        )
        metrics_missing_but_context_resolvable = bool(
            not query_intent.matched_metrics
            and session_state.metrics
            and (
                current_filter_fields
                or current_has_dimensions
                or current_has_time
                or current_has_version
                or current_has_sort
                or current_has_limit
                or query_intent.has_follow_up_cue
            )
        )
        introduces_new_topic_signal = bool(
            (parsed_domain_known and query_intent.subject_domain != session_state.subject_domain)
            or (current_metrics and not reused_metric_count)
            or (current_entities and not reused_entity_count and not query_intent.has_follow_up_cue)
        )
        is_short_followup_fragment = bool(
            len(query_intent.normalized_question) <= 12
            and (
                query_intent.has_follow_up_cue
                or only_updates_filters
                or only_updates_dimensions
                or only_updates_time
                or only_updates_version
                or only_updates_sort
                or only_updates_limit
            )
        )
        return {
            "has_session": True,
            "parsed_domain_known": parsed_domain_known,
            "domain_changed": parsed_domain_known and query_intent.subject_domain != session_state.subject_domain,
            "new_metrics": [
                item for item in query_intent.matched_metrics if item not in session_state.metrics
            ],
            "reused_metrics": [
                item for item in query_intent.matched_metrics if item in session_state.metrics
            ],
            "new_entities": [
                item for item in query_intent.matched_entities if item not in session_state.entities
            ],
            "requested_dimensions": query_intent.requested_dimensions,
            "reused_entities": [
                item for item in query_intent.matched_entities if item in session_state.entities
            ],
            "new_filter_fields": sorted(current_filter_fields - previous_filter_fields),
            "reused_filter_fields": sorted(current_filter_fields.intersection(previous_filter_fields)),
            "has_follow_up_cue": query_intent.has_follow_up_cue,
            "has_explicit_slots": query_intent.has_explicit_slots,
            "only_updates_filters": only_updates_filters,
            "only_updates_dimensions": only_updates_dimensions,
            "only_updates_time": only_updates_time,
            "only_updates_version": only_updates_version,
            "only_updates_sort": only_updates_sort,
            "only_updates_limit": only_updates_limit,
            "has_independent_target": bool(query_intent.matched_metrics or parsed_domain_known),
            "can_execute_without_context": can_execute_without_context,
            "is_short_followup_fragment": is_short_followup_fragment,
            "explicit_time_or_version_slot": current_has_time or current_has_version,
            "metric_overlap_ratio": round(reused_metric_count / max(1, len(current_metrics)), 3),
            "entity_overlap_ratio": round(reused_entity_count / max(1, len(current_entities)), 3),
            "filter_overlap_ratio": round(reused_filter_count / max(1, len(current_filter_fields)), 3),
            "metrics_missing_but_context_resolvable": metrics_missing_but_context_resolvable,
            "introduces_new_topic_signal": introduces_new_topic_signal,
            "time_grain_changed": (
                previous_has_time
                and current_has_time
                and query_intent.time_context.grain != session_state.time_context.grain
            ),
            "version_changed": bool(
                current_has_version
                and query_intent.version_context.value
                != (session_state.version_context.value if session_state.version_context else None)
            ),
            "time_was_implicit": previous_has_time and not current_has_time,
            "version_was_implicit": previous_has_version and not current_has_version,
        }

    def infer_domain(
        self,
        matched_metrics: list[str],
        matched_entities: list[str],
        requested_dimensions: list[str] | None = None,
        filters: list[FilterItem] | None = None,
        question: str | None = None,
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
            hint_keywords = [str(item).lower() for item in hint.get("keywords", []) if item]
            keyword_match = bool(question and any(keyword in question for keyword in hint_keywords))
            if (
                hint_entities.intersection(entity_set)
                or hint_filter_fields.intersection(filter_fields)
                or keyword_match
            ):
                hint_counter[domain] += int(hint.get("weight", 1))

        if hint_counter:
            if (
                session_state is not None
                and requested_dimensions
                and not matched_metrics
                and not filters
            ):
                return session_state.subject_domain
            return hint_counter.most_common(1)[0][0]

        if session_state and self.domain_inference.get("fallback_to_session", True):
            return session_state.subject_domain

        return "unknown"

    def suggest_dimensions(
        self,
        subject_domain: str,
        requested_dimensions: list[str],
        matched_entities: list[str],
        filter_fields: set[str],
        time_grain: str,
    ) -> list[str]:
        profile = self.query_profiles.get(subject_domain, {})
        preferences = profile.get("dimension_preferences", [])
        dimensions = self._unique_strings(requested_dimensions)
        entities = set(matched_entities)

        for rule in preferences:
            required_entities = set(rule.get("entities", []))
            excluded_filter_fields = set(rule.get("exclude_filter_fields", []))
            rule_time_grain = rule.get("time_grain")
            add_dimensions = [item for item in rule.get("add_dimensions", []) if item]

            if required_entities and not required_entities.issubset(entities):
                continue
            if rule_time_grain and rule_time_grain != time_grain:
                continue
            if excluded_filter_fields.intersection(filter_fields) and not set(add_dimensions).intersection(dimensions):
                continue

            for dimension in add_dimensions:
                if dimension not in dimensions:
                    dimensions.append(dimension)

        return dimensions

    def extract_dimensions(self, question: str) -> list[str]:
        dimensions: list[str] = []
        for rule in self.dimension_extractors:
            dimension = self._extract_dimension(question, rule)
            if dimension and dimension not in dimensions:
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

    def extract_analysis_mode(self, question: str) -> str | None:
        for rule in self.analysis_extractors:
            source = self._prepare_text(question, rule)
            flags = 0
            if "ignorecase" in rule.get("flags", []):
                flags |= re.IGNORECASE
            if re.search(rule.get("pattern", ""), source, flags):
                mode = rule.get("mode")
                if isinstance(mode, str) and mode:
                    return mode
        return None

    def extract_sort(self, question: str, matched_metrics: list[str] | None = None) -> list[SortItem]:
        sort_items: list[SortItem] = []
        for rule in self.sort_extractors:
            extracted = self._extract_sort_rule(question, rule, matched_metrics or [])
            if extracted is not None:
                sort_items.append(extracted)
        return self._sanitize_sort(sort_items, allowed_fields=set())

    def extract_limit(self, question: str) -> int | None:
        for rule in self.limit_extractors:
            extracted = self._extract_limit_rule(question, rule)
            if extracted is not None:
                return extracted
        return None

    def apply_domain_constraints(self, query_plan: QueryPlan) -> QueryPlan:
        profile = self.query_profiles.get(query_plan.subject_domain, {})
        compiled = query_plan.model_copy(deep=True)

        compiled = self._apply_explicit_source_table(compiled)

        compiled = self._inject_time_filters(compiled, profile)
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
            required_metrics = set(rule.get("metrics", []))
            required_entities = set(rule.get("entities", []))
            excluded_entities = set(rule.get("exclude_entities", []))
            excluded_filter_fields = set(rule.get("exclude_filter_fields", []))
            missing_version_context = bool(rule.get("missing_version_context", False))

            if required_metrics and not required_metrics.intersection(compiled.metrics):
                continue
            if required_entities and not required_entities.issubset(entities):
                continue
            if excluded_entities.intersection(entities):
                continue
            if excluded_filter_fields.intersection(filter_fields):
                continue
            if missing_version_context and compiled.version_context is not None:
                continue

            compiled.need_clarification = True
            compiled.question_type = rule.get("question_type", "clarification_needed")
            compiled.reason_code = rule.get("reason_code")
            compiled.reason = rule.get("reason")
            compiled.clarification_question = rule.get("clarification_question")
            break

        return compiled

    def _apply_explicit_source_table(self, query_plan: QueryPlan) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        explicit_source = next(
            (
                item.value
                for item in compiled.filters
                if item.field in {"source_table", "demand_source"}
                and item.op == "="
                and isinstance(item.value, str)
                and self.is_known_table(item.value)
            ),
            None,
        )
        if not explicit_source:
            return compiled

        allowed_domain_tables = set(self.domain_tables(compiled.subject_domain))
        if allowed_domain_tables and explicit_source not in allowed_domain_tables:
            return compiled

        demand_tables = {"p_demand", "v_demand"}
        other_tables = [
            table
            for table in compiled.tables
            if table != explicit_source and table not in demand_tables
        ]
        compiled.tables = [explicit_source, *other_tables]
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
        version_context: VersionContext | None,
    ) -> VersionContext | None:
        if version_context is None or not version_context.value:
            return None
        profile_version_field = self.query_profile(domain_name).get("version_field")
        version_field = version_context.field or profile_version_field
        if not version_field:
            return None
        allowed_fields = set(self.profile_allowed_fields(domain_name))
        if profile_version_field and version_field != profile_version_field and version_field not in allowed_fields:
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

    def _extract_sort_rule(
        self,
        question: str,
        rule: dict,
        matched_metrics: list[str],
    ) -> SortItem | None:
        source = self._prepare_text(question, rule)
        flags = 0
        if "ignorecase" in rule.get("flags", []):
            flags |= re.IGNORECASE
        pattern = rule.get("pattern", "")
        if not pattern or not re.search(pattern, source, flags):
            return None

        field = rule.get("field")
        if field == "__matched_metric__":
            for metric in matched_metrics:
                resolved = self.metric_column(metric)
                if resolved:
                    field = resolved
                    break
        if not field:
            return None

        order = rule.get("order", "desc")
        return SortItem(field=field, order=order)

    def _extract_limit_rule(self, question: str, rule: dict) -> int | None:
        source = self._prepare_text(question, rule)
        flags = 0
        if "ignorecase" in rule.get("flags", []):
            flags |= re.IGNORECASE
        match = re.search(rule.get("pattern", ""), source, flags)
        if not match:
            return None
        if "value" in rule:
            try:
                return max(1, int(rule["value"]))
            except (TypeError, ValueError):
                return None
        try:
            value = int(match.group(1))
        except (IndexError, TypeError, ValueError):
            return None
        return max(1, value)

    def _metric_resolution_rule_matches(
        self,
        normalized_question: str,
        filters: list[FilterItem],
        rule: dict,
    ) -> bool:
        text_any = [str(item).lower() for item in rule.get("text_any", []) if item]
        text_all = [str(item).lower() for item in rule.get("text_all", []) if item]
        text_none = [str(item).lower() for item in rule.get("text_none", []) if item]

        if text_any and not any(token in normalized_question for token in text_any):
            return False
        if text_all and not all(token in normalized_question for token in text_all):
            return False
        if text_none and any(token in normalized_question for token in text_none):
            return False

        required_filters = rule.get("required_filters", [])
        for expected in required_filters:
            if not isinstance(expected, dict):
                return False
            field = expected.get("field")
            op = expected.get("op", "=")
            value = expected.get("value")
            if not any(
                item.field == field and item.op == op and item.value == value
                for item in filters
            ):
                return False
        return True

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

    def _extract_dimension(self, question: str, rule: dict) -> str | None:
        field = rule.get("field")
        if not field:
            return None

        rule_type = rule.get("type", "regex")
        if rule_type == "regex":
            flags = 0
            if "ignorecase" in rule.get("flags", []):
                flags |= re.IGNORECASE
            source = self._prepare_text(question, rule)
            if re.search(rule.get("pattern", ""), source, flags):
                return field

        if rule_type == "keyword":
            source = self._prepare_text(question, rule)
            for candidate in rule.get("candidates", []):
                normalized_candidate = candidate.upper() if "uppercase" in rule.get("flags", []) else candidate
                if self._contains_candidate(source, normalized_candidate):
                    return field

        return None

    def _extract_regex_value(self, question: str, rule: dict) -> str | None:
        flags = 0
        if "ignorecase" in rule.get("flags", []):
            flags |= re.IGNORECASE
        source = self._prepare_text(question, rule)
        match = re.search(rule.get("pattern", ""), source, flags)
        if not match:
            return None
        if "value" in rule:
            return str(rule["value"])
        try:
            value = match.group(1)
        except IndexError:
            return match.group(0)
        value_template = rule.get("value_template")
        if isinstance(value_template, str):
            return value_template.replace("{match}", value)
        return value

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

        if rule_type == "calendar_year":
            year = int(match.group(1))
            start = f"{year:04d}-01-01"
            end = f"{year:04d}-12-31"
            return {
                "filter": FilterItem(field=field, op="between", value=[start, end]) if field else None,
                "context": TimeContext(grain="day", range=TimeRange(start=start, end=end)),
            }

        if rule_type == "short_calendar_year":
            year = 2000 + int(match.group(1))
            start = f"{year:04d}-01-01"
            end = f"{year:04d}-12-31"
            return {
                "filter": FilterItem(field=field, op="between", value=[start, end]) if field else None,
                "context": TimeContext(grain="day", range=TimeRange(start=start, end=end)),
            }

        if rule_type == "compact_month":
            value = match.group(1)
            return {
                "filter": FilterItem(field=field, op="=", value=value) if field else None,
                "context": TimeContext(grain="month", range=TimeRange(start=value, end=value)),
            }

        if rule_type == "relative_recent_months":
            try:
                month_count = max(1, int(match.group(1)))
            except (IndexError, TypeError, ValueError):
                return None
            today = date.today()
            current_month_index = today.year * 12 + (today.month - 1)
            start_month_index = current_month_index - (month_count - 1)
            start_year = start_month_index // 12
            start_month = start_month_index % 12 + 1
            start = f"{start_year:04d}-{start_month:02d}-01"
            end_day = calendar.monthrange(today.year, today.month)[1]
            end = f"{today.year:04d}-{today.month:02d}-{end_day:02d}"
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
        allowed_fields = set(profile.get("allowed_fields", []))
        if profile.get("version_field") and version_field != profile.get("version_field") and version_field not in allowed_fields:
            return compiled

        existing = {
            f"{item.field}:{item.op}:{repr(item.value)}"
            for item in compiled.filters
        }
        version_value = compiled.version_context.value
        if version_value.startswith("LATEST_N:"):
            try:
                latest_count = max(1, int(version_value.removeprefix("LATEST_N:")))
            except ValueError:
                return compiled
            source_table = next(
                (
                    item.value
                    for item in compiled.filters
                    if item.field in {"source_table", "demand_source"}
                    and item.op == "="
                    and isinstance(item.value, str)
                ),
                None,
            )
            version_filter = FilterItem(
                field=version_field,
                op="latest_n",
                value={"count": latest_count, "source_table": source_table},
            )
        else:
            version_filter = FilterItem(field=version_field, op="=", value=version_value)
        version_key = f"{version_filter.field}:{version_filter.op}:{repr(version_filter.value)}"
        if version_key not in existing:
            compiled.filters = [
                item for item in compiled.filters if item.field != version_filter.field
            ] + [version_filter]
        return compiled

    def _inject_time_filters(self, query_plan: QueryPlan, profile: dict) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        time_context = compiled.time_context
        if time_context.grain == "unknown" or time_context.range is None:
            return compiled

        start = time_context.range.start
        end = time_context.range.end
        if not start and not end:
            return compiled

        time_fields = self.time_filter_fields(compiled.subject_domain)
        if not time_fields:
            return compiled

        allowed_fields = set(profile.get("allowed_fields", []))
        candidate_fields = [
            field
            for field in time_fields
            if not allowed_fields or field in allowed_fields
        ]
        if not candidate_fields:
            candidate_fields = time_fields

        preferred_field = candidate_fields[0]
        if time_context.grain == "month" and len(candidate_fields) > 1:
            month_field = next((field for field in candidate_fields if "month" in field.lower()), None)
            if month_field:
                preferred_field = month_field
        elif time_context.grain == "day":
            day_field = next((field for field in candidate_fields if "date" in field.lower()), None)
            if day_field:
                preferred_field = day_field

        time_filter = self._build_time_filter(preferred_field, start=start, end=end)
        if time_filter is None:
            return compiled

        existing = {
            f"{item.field}:{item.op}:{repr(item.value)}"
            for item in compiled.filters
        }
        time_key = f"{time_filter.field}:{time_filter.op}:{repr(time_filter.value)}"
        if time_key in existing:
            compiled.filters = [
                item for item in compiled.filters if item.field not in set(candidate_fields) or item.field == time_filter.field
            ]
            return compiled

        compiled.filters = [
            item for item in compiled.filters if item.field not in set(candidate_fields)
        ] + [time_filter]
        return compiled

    def _build_time_filter(
        self,
        field: str,
        start: str | None,
        end: str | None,
    ) -> FilterItem | None:
        if not field:
            return None
        if field == "demand_month":
            compact_start = self._compact_month_value(start)
            compact_end = self._compact_month_value(end)
            if compact_start and compact_end:
                if compact_start == compact_end:
                    return FilterItem(field=field, op="=", value=compact_start)
                return FilterItem(field=field, op="between", value=[compact_start, compact_end])
            if compact_start:
                return FilterItem(field=field, op=">=", value=compact_start)
            if compact_end:
                return FilterItem(field=field, op="<=", value=compact_end)
        if start and end:
            if start == end:
                return FilterItem(field=field, op="=", value=start)
            return FilterItem(field=field, op="between", value=[start, end])
        if start:
            return FilterItem(field=field, op=">=", value=start)
        if end:
            return FilterItem(field=field, op="<=", value=end)
        return None

    def _compact_month_value(self, value: str | None) -> str | None:
        if not value:
            return None
        if re.fullmatch(r"20\d{2}(?:0[1-9]|1[0-2])", value):
            return value
        match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])(?:-\d{2})?", value)
        if match:
            return f"{match.group(1)}{match.group(2)}"
        return None

    def _inject_default_sort(self, query_plan: QueryPlan, profile: dict) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        if compiled.sort:
            return compiled
        if compiled.analysis_mode == "compare" and not compiled.dimensions:
            return compiled
        default_sort = profile.get("default_sort", [])
        if not default_sort:
            return compiled
        compiled.sort = [SortItem(**item) for item in default_sort if isinstance(item, dict)]
        return compiled

    def _load_tables_metadata(self) -> dict:
        try:
            return json.loads(TABLES_METADATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _extract_table_fields(self, payload: dict) -> list[str]:
        fields: list[str] = []
        for raw_column in payload.get("columns", []):
            if not raw_column:
                continue
            column_name = str(raw_column).split("(", 1)[0].strip()
            if column_name and column_name not in fields:
                fields.append(column_name)
        return fields

    def _unique_strings(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
