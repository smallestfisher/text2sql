from __future__ import annotations

from collections import deque
import calendar
import re

from backend.app.models.semantic_types import FilterItem
from backend.app.models.semantic_types import SortItem
from backend.app.models.sql_generation_context import SqlGenerationContext
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
        self.reload()

    def reload(self) -> None:
        self.tables_metadata = self._load_tables_metadata()
        self.subject_domain_catalog = self.metadata_registry.subject_domains
        self.graph_nodes = set(self.tables_metadata.keys())
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

    def subject_domains(self) -> list[str]:
        return list(self.subject_domain_catalog)

    def is_known_domain(self, domain_name: str | None) -> bool:
        return bool(domain_name and domain_name in self.subject_domain_catalog)

    def clamp_limit(self, domain_name: str, limit: int | None, default_value: int = 200) -> int:
        _ = domain_name
        if limit is None or limit <= 0:
            return default_value
        return min(limit, default_value)

    def sanitize_sql_context(
        self,
        sql_context: SqlGenerationContext,
        default_limit: int = 200,
    ) -> SqlGenerationContext:
        compiled = sql_context.model_copy(deep=True)
        compiled.tables = [
            table
            for table in self._unique_strings(compiled.tables)
            if self.is_known_table(table)
        ]
        allowed_fields = self.allowed_fields_for_context(compiled)
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

    def table_fields(self, table_name: str) -> list[str]:
        return list(self.table_field_catalog.get(table_name, []))

    def table_time_field(self, table_name: str, field_name: str) -> dict | None:
        if not field_name:
            return None
        metadata = self.table_time_field_catalog.get(table_name, {}).get(field_name)
        if metadata is None:
            return None
        return dict(metadata)

    def is_formatted_string_time_field(self, metadata: dict | None) -> bool:
        if not metadata:
            return False
        normalized_format = str(metadata.get("format") or "").strip().upper()
        return normalized_format in {"YYYYMM", "YYYY-MM", "YYYYMMDD", "YYYY-MM-DD"}

    def resolve_time_field_candidates(
        self,
        domain_name: str,
        table_names: list[str],
        logical_field: str | None = None,
        *,
        grain: str | None = None,
    ) -> list[dict]:
        _ = domain_name
        candidates: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for table_name in table_names:
            for field_name, metadata in self.table_time_field_catalog.get(table_name, {}).items():
                if not self._time_field_matches_logical_field(field_name, metadata, logical_field, grain=grain):
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
                        "semantic_names": list(metadata.get("semantic_names", [])),
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
        for table_name in table_names:
            for column_name in self.table_fields(table_name):
                time_metadata = self.table_time_field(table_name, column_name) or {}
                semantic_names = {
                    str(item).strip().lower()
                    for item in time_metadata.get("semantic_names", [])
                    if str(item).strip()
                }
                if (
                    column_name.lower() == lowered_field
                    or lowered_field in semantic_names
                ):
                    candidates.add(column_name)
        return {item for item in candidates if item}

    def allowed_fields_for_context(self, context) -> set[str]:
        allowed_fields: set[str] = set()
        for table_name in context.tables:
            allowed_fields.update(self.table_fields(table_name))
        allowed_fields.update(context.dimensions)
        allowed_fields.update(item.field for item in context.filters)
        allowed_fields.update(item.field for item in context.sort)
        if context.version_context and context.version_context.field:
            allowed_fields.add(context.version_context.field)
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
        logical_field: str | None,
        *,
        grain: str | None = None,
    ) -> bool:
        configured_grain = str(metadata.get("grain") or "").lower()
        if grain and configured_grain != str(grain).strip().lower():
            return False
        if not logical_field:
            return True
        lowered = logical_field.strip().lower()
        if field_name.lower() == lowered:
            return True
        semantic_names = {
            str(item).strip().lower()
            for item in metadata.get("semantic_names", []) or []
            if str(item).strip()
        }
        return lowered in semantic_names

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
                    "semantic_names": self._extract_semantic_names(raw_metadata),
                }

        return normalized

    def _extract_semantic_names(self, metadata: dict, field_name: str | None = None) -> list[str]:
        values = metadata.get("semantic_names", metadata.get("aliases", []))
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            values = []
        result: list[str] = []
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        if field_name and field_name not in result:
            result.append(field_name)
        return result

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
