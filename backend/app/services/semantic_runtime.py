from __future__ import annotations

from collections import deque
import calendar
import re

from backend.app.models.query_plan import FilterItem
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import SortItem
from backend.app.models.query_plan import VersionContext
from backend.app.services.metadata_registry import MetadataRegistry


class SemanticRuntime:
    """Runtime helpers for the table schema boundary.

    Business interpretation now comes from retrieved knowledge, join patterns
    and examples. This class intentionally keeps only deterministic table,
    field, join and time-literal utilities used by prompts and validation.
    """

    def __init__(
        self,
        domain_config: dict,
        metadata_registry: MetadataRegistry | None = None,
    ) -> None:
        self.domain_config = domain_config
        self.metadata_registry = metadata_registry or MetadataRegistry()
        self.graph_nodes = set(domain_config.get("semantic_graph", {}).get("nodes", []))
        self.graph_edges = domain_config.get("semantic_graph", {}).get("edges", [])
        self.tables_metadata = self._load_tables_metadata()
        if not self.graph_nodes:
            self.graph_nodes = set(self.tables_metadata.keys())
        if not self.graph_edges:
            self.graph_edges = self._relationship_edges(self.tables_metadata)
        self.table_field_catalog = {
            table_name: self._extract_table_fields(payload)
            for table_name, payload in self.tables_metadata.items()
            if isinstance(payload, dict)
        }
        self.table_time_field_catalog = {
            table_name: self._extract_table_time_fields(payload)
            for table_name, payload in self.tables_metadata.items()
            if isinstance(payload, dict)
        }
        self.table_domain_catalog = self._build_table_domain_catalog()

    def default_limit(self, domain_name: str, default_value: int = 200) -> int:
        _ = domain_name
        return default_value

    def max_limit(self, domain_name: str, default_value: int = 200) -> int:
        _ = domain_name
        return default_value

    def clamp_limit(self, domain_name: str, limit: int | None, default_value: int = 200) -> int:
        _ = domain_name
        if limit is None or limit <= 0:
            return default_value
        return min(limit, default_value)

    def sanitize_query_plan(
        self,
        query_plan: QueryPlan,
        default_limit: int = 200,
    ) -> QueryPlan:
        compiled = query_plan.model_copy(deep=True)
        compiled.tables = [
            table
            for table in self._unique_strings(compiled.tables)
            if self.is_known_table(table)
        ]
        allowed_fields = self.allowed_fields_for_plan(compiled)
        compiled.dimensions = self._sanitize_dimensions(compiled.dimensions, allowed_fields)
        compiled.filters = self._sanitize_filters(compiled.filters, allowed_fields)
        compiled.sort = self._sanitize_sort(compiled.sort, allowed_fields)
        compiled.limit = self.clamp_limit(
            compiled.subject_domain,
            compiled.limit,
            default_value=default_limit,
        )
        compiled.join_path = self.resolve_join_path(compiled.tables)
        return compiled

    def query_profile(self, domain_name: str) -> dict:
        _ = domain_name
        return {}

    def is_known_domain(self, domain_name: str) -> bool:
        return domain_name == "unknown" or bool(domain_name)

    def domain_tables(self, domain_name: str) -> list[str]:
        _ = domain_name
        return []

    def table_domains(self, table_name: str) -> list[str]:
        return list(self.table_domain_catalog.get(table_name, []))

    def _build_table_domain_catalog(self) -> dict[str, list[str]]:
        table_domains: dict[str, list[str]] = {}
        for template in self.metadata_registry.examples_template:
            if not isinstance(template, dict):
                continue
            self._add_table_domains(
                table_domains,
                tables=self._tables_from_template_sql(template.get("sql")),
                domains=[str(template.get("subject_domain") or "")],
            )
        for entry in self.metadata_registry.business_knowledge_entries:
            if not isinstance(entry, dict):
                continue
            self._add_table_domains(
                table_domains,
                tables=[str(item) for item in entry.get("tables", []) if item],
                domains=[str(item) for item in entry.get("domains", []) if item],
            )
        for pattern in self.metadata_registry.join_patterns:
            if not isinstance(pattern, dict):
                continue
            self._add_table_domains(
                table_domains,
                tables=[str(item) for item in pattern.get("tables", []) if item],
                domains=[str(item) for item in pattern.get("domains", []) if item],
            )
        return table_domains

    def _add_table_domains(self, table_domains: dict[str, list[str]], *, tables: list[str], domains: list[str]) -> None:
        normalized_domains = [
            domain_name
            for domain_name in self._unique_strings(domains)
            if domain_name != "unknown"
        ]
        for table_name in self._unique_strings(tables):
            if table_name not in self.tables_metadata:
                continue
            for domain_name in normalized_domains:
                table_domains.setdefault(table_name, [])
                if domain_name not in table_domains[table_name]:
                    table_domains[table_name].append(domain_name)

    def _tables_from_template_sql(self, sql: object) -> list[str]:
        if not isinstance(sql, str):
            return []
        normalized_sql = sql.replace('"', " ")
        candidates = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", normalized_sql, flags=re.IGNORECASE)
        return [table_name for table_name in self._unique_strings(candidates) if table_name in self.tables_metadata]

    def metric_column(self, metric_name: str) -> str:
        return metric_name

    def metric_aggregate_function(self, metric_name: str) -> str:
        _ = metric_name
        return "SUM"

    def metric_act_type_scope(self, metric_name: str) -> str | None:
        _ = metric_name
        return None

    def is_known_metric(self, metric_name: str) -> bool:
        _ = metric_name
        return False

    def metric_tables(self, metric_name: str) -> list[str]:
        _ = metric_name
        return []

    def metric_expression_columns(self, metric_name: str, table_names: list[str] | None = None) -> set[str]:
        _ = metric_name
        _ = table_names
        return set()

    def profile_allowed_fields(self, domain_name: str) -> list[str]:
        _ = domain_name
        return []

    def profile_field_aliases(self, domain_name: str) -> dict[str, list[str]]:
        _ = domain_name
        return {}

    def semantic_field_metadata(
        self,
        subject_domain: str | None = None,
        role: str | None = None,
    ) -> list[dict]:
        _ = subject_domain
        _ = role
        return []

    def semantic_field_aliases(
        self,
        subject_domain: str | None = None,
        role: str | None = None,
    ) -> dict[str, list[str]]:
        _ = subject_domain
        _ = role
        return {}

    def normalize_field_name(self, field_name: str, subject_domain: str) -> str:
        _ = subject_domain
        return field_name

    def resolve_tables_for_plan(self, domain_name: str, metrics: list[str]) -> list[str]:
        _ = domain_name
        _ = metrics
        return []

    def time_filter_fields(self, domain_name: str) -> list[str]:
        _ = domain_name
        return []

    def warn_if_missing_time_filter(self, domain_name: str) -> bool:
        _ = domain_name
        return False

    def table_fields(self, table_name: str) -> list[str]:
        return list(self.table_field_catalog.get(table_name, []))

    def table_time_fields(self, table_name: str) -> dict[str, dict]:
        return {
            field_name: dict(metadata)
            for field_name, metadata in self.table_time_field_catalog.get(table_name, {}).items()
        }

    def table_time_field(self, table_name: str, field_name: str) -> dict | None:
        if not field_name:
            return None
        metadata = self.table_time_field_catalog.get(table_name, {}).get(field_name)
        if metadata is None:
            return None
        return dict(metadata)

    def resolve_time_field_candidates(
        self,
        domain_name: str,
        table_names: list[str],
        logical_field: str,
    ) -> list[dict]:
        _ = domain_name
        candidates: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for table_name in table_names:
            for field_name, metadata in self.table_time_field_catalog.get(table_name, {}).items():
                if not self._time_field_matches_logical_field(field_name, metadata, logical_field):
                    continue
                key = (table_name, field_name)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "table": table_name,
                        "field": field_name,
                        "qualified_field": f"{table_name}.{field_name}",
                        "grain": metadata.get("grain"),
                        "format": metadata.get("format"),
                    }
                )
        return candidates

    def compact_month_value(self, value: str | None) -> str | None:
        return self._compact_month_value(value)

    def format_time_literal(self, value: str | None, target_format: str | None) -> str | None:
        if not value or not target_format:
            return None
        normalized_format = str(target_format).strip().upper()
        compact_day_match = re.fullmatch(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", value)
        iso_day_match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", value)
        compact_month_match = re.fullmatch(r"(20\d{2})(0[1-9]|1[0-2])", value)
        iso_month_match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", value)

        if normalized_format == "YYYYMM":
            if compact_month_match:
                return value
            if iso_month_match:
                return f"{iso_month_match.group(1)}{iso_month_match.group(2)}"
            if compact_day_match:
                return f"{compact_day_match.group(1)}{compact_day_match.group(2)}"
            if iso_day_match:
                return f"{iso_day_match.group(1)}{iso_day_match.group(2)}"
            return None

        if normalized_format == "YYYY-MM":
            if compact_month_match:
                return f"{compact_month_match.group(1)}-{compact_month_match.group(2)}"
            if iso_month_match:
                return value
            if compact_day_match:
                return f"{compact_day_match.group(1)}-{compact_day_match.group(2)}"
            if iso_day_match:
                return f"{iso_day_match.group(1)}-{iso_day_match.group(2)}"
            return None

        if normalized_format == "YYYYMMDD":
            if compact_day_match:
                return value
            if iso_day_match:
                return f"{iso_day_match.group(1)}{iso_day_match.group(2)}{iso_day_match.group(3)}"
            return None

        if normalized_format == "YYYY-MM-DD":
            if compact_day_match:
                return (
                    f"{compact_day_match.group(1)}-"
                    f"{compact_day_match.group(2)}-"
                    f"{compact_day_match.group(3)}"
                )
            if iso_day_match:
                return value
            return None

        return None

    def month_range_literals(
        self,
        value: str | None,
        target_format: str | None,
    ) -> tuple[str, str] | None:
        compact_month = self._compact_month_value(value)
        if not compact_month or not target_format:
            return None
        year = int(compact_month[:4])
        month = int(compact_month[4:6])
        end_day = calendar.monthrange(year, month)[1]
        start_iso = f"{year:04d}-{month:02d}-01"
        end_iso = f"{year:04d}-{month:02d}-{end_day:02d}"
        start_literal = self.format_time_literal(start_iso, target_format)
        end_literal = self.format_time_literal(end_iso, target_format)
        if not start_literal or not end_literal:
            return None
        return start_literal, end_literal

    def resolve_field_candidates(
        self,
        domain_name: str,
        table_names: list[str],
        logical_field: str,
    ) -> set[str]:
        _ = domain_name
        candidates = {logical_field}
        lowered_field = logical_field.lower()
        aliases = self._logical_field_aliases(logical_field)
        candidates.update(aliases)
        lowered_aliases = {item.lower() for item in aliases}
        for table_name in table_names:
            for column_name in self.table_fields(table_name):
                if column_name.lower() == lowered_field or column_name.lower() in lowered_aliases:
                    candidates.add(column_name)
        return {item for item in candidates if item}

    def is_dynamic_version_context(self, version_context: VersionContext | None) -> bool:
        return bool(
            version_context
            and isinstance(version_context.value, str)
            and version_context.value.startswith("LATEST_N:")
        )

    def allowed_fields_for_plan(self, query_plan: QueryPlan) -> set[str]:
        allowed_fields: set[str] = set()
        for table_name in query_plan.tables:
            allowed_fields.update(self.table_fields(table_name))
        allowed_fields.update(query_plan.dimensions)
        allowed_fields.update(item.field for item in query_plan.filters)
        allowed_fields.update(item.field for item in query_plan.sort)
        if query_plan.version_context and query_plan.version_context.field:
            allowed_fields.add(query_plan.version_context.field)
        return {item for item in allowed_fields if item}

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

    def _time_field_matches_logical_field(
        self,
        field_name: str,
        metadata: dict,
        logical_field: str,
    ) -> bool:
        grain = str(metadata.get("grain") or "").lower()
        lowered = field_name.lower()
        if logical_field == "biz_date":
            return grain == "day" or "date" in lowered
        if logical_field == "biz_month":
            return grain == "month" or "month" in lowered or grain == "day"
        if logical_field == "demand_month":
            return grain == "month" or lowered == "month" or "month" in lowered
        return lowered == logical_field.lower()

    def _logical_field_aliases(self, logical_field: str) -> set[str]:
        if logical_field == "biz_date":
            return {"work_date", "report_date", "PLAN_date", "SALE_date"}
        if logical_field == "biz_month":
            return {"work_date", "report_date", "PLAN_date", "plan_month", "MONTH", "SALE_date"}
        if logical_field == "demand_month":
            return {"MONTH"}
        return set()

    def _compact_month_value(self, value: str | None) -> str | None:
        if not value:
            return None
        if re.fullmatch(r"20\d{2}(?:0[1-9]|1[0-2])", value):
            return value
        match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])(?:-\d{2})?", value)
        if match:
            return f"{match.group(1)}{match.group(2)}"
        return None

    def _load_tables_metadata(self) -> dict:
        return self.metadata_registry.tables_metadata

    def _extract_table_fields(self, payload: dict) -> list[str]:
        fields: list[str] = []
        for raw_column in payload.get("columns", []):
            if not raw_column:
                continue
            column_name = str(raw_column).split("(", 1)[0].strip()
            if column_name and column_name not in fields:
                fields.append(column_name)
        return fields

    def _extract_table_time_fields(self, payload: dict) -> dict[str, dict]:
        fields = set(self._extract_table_fields(payload))
        raw_time_fields = payload.get("time_fields", {})
        normalized: dict[str, dict] = {}
        if isinstance(raw_time_fields, dict):
            for raw_field_name, raw_metadata in raw_time_fields.items():
                field_name = str(raw_field_name).strip()
                if not field_name or field_name not in fields or not isinstance(raw_metadata, dict):
                    continue
                grain = str(raw_metadata.get("grain", "")).strip().lower() or None
                value_format = str(raw_metadata.get("format", "")).strip().upper() or None
                normalized[field_name] = {
                    "grain": grain,
                    "format": value_format,
                }

        legacy_mappings = [
            ("date_col", "day"),
            ("month_col", "month"),
        ]
        for key, grain in legacy_mappings:
            field_name = str(payload.get(key, "")).strip()
            if field_name and field_name in fields and field_name not in normalized:
                normalized[field_name] = {
                    "grain": grain,
                    "format": None,
                }
        return normalized

    def _relationship_edges(self, tables_metadata: dict) -> list[dict[str, str]]:
        edges: list[dict[str, str]] = []
        table_names = set(tables_metadata.keys())
        for source_table, payload in tables_metadata.items():
            if not isinstance(payload, dict):
                continue
            relationships = payload.get("relationships", {})
            if not isinstance(relationships, dict):
                continue
            for source_field, raw_targets in relationships.items():
                for target in str(raw_targets or "").split(","):
                    target = target.strip()
                    if not target or "." not in target:
                        continue
                    target_table, target_field = target.split(".", 1)
                    if target_table not in table_names:
                        continue
                    edges.append(
                        {
                            "from": source_table,
                            "to": target_table,
                            "on": f"{source_table}.{source_field} = {target_table}.{target_field}",
                            "source": source_table,
                            "target": target_table,
                            "source_field": str(source_field),
                            "target_field": target_field,
                        }
                    )
        return edges

    def _unique_strings(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
