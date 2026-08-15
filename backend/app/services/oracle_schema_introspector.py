from __future__ import annotations

from copy import deepcopy

from sqlalchemy import inspect

from backend.app.services.database_connector import DatabaseConnector

# Fields that are pure physical fact produced by introspection. They are
# overwritten from the introspector output on every sync even when the table
# already carries human-authored business content.
_PHYSICAL_FIELDS = (
    "schema",
    "table_name",
    "column_details",
    "source",
)
# Fields that are business semantics authored by humans in the Semantic Studio
# UI. Sync must NEVER overwrite them; introspection can at most suggest new
# candidates (recorded as warnings), never replace what a human wrote. MAIN_KEY
# is intentionally NOT here: it is a physical fact (the PK) that backs up the
# human value when absent, so it is managed by its own rule below.
_BUSINESS_FIELDS = (
    "description",
    "columns",
    "time_fields",
    "month_col",
    "version_col",
    "date_col",
    "relationships",
)


def _column_name(item: str) -> str:
    """Extract the bare physical column name from a ``columns`` entry.

    Semantic Studio stores columns either as a plain name (``"MONTH"``) or as
    a name with a business annotation in parens (``"MONTH (需求起始月份)"``).
    The physical name is what precedes the first ``" ("``.
    """
    text = str(item).strip()
    paren = text.find(" (")
    if paren == -1:
        paren = text.find("(")
    return text[:paren].strip().upper() if paren != -1 else text.upper()


def merge_tables_metadata(
    existing: dict,
    introspected: dict,
    data_source_id: str,
) -> tuple[dict, list[str]]:
    """Three-way merge introspected physical facts into stored tables_metadata.

    The stored document mixes physical facts (column names, PK, schema) with
    human-authored business content (description, time_fields, business join
    relationships, Chinese column annotations). Sync is allowed to refresh
    physical facts and surface drift, but must never replace what a human
    wrote in the Semantic Studio UI.

    Args:
        existing: the current ``tables_metadata`` document (all sources).
        introspected: per-table dicts from ``OracleSchemaIntrospector.inspect``
            (already tagged with ``data_source_id``/``dialect`` by the caller).
        data_source_id: the source whose tables are being merged.

    Returns:
        ``(merged_document, warnings)`` — the new full ``tables_metadata`` and
        a list of human-facing suggestions/drift notices for the UI.
    """
    warnings: list[str] = []
    # Index existing tables that belong to THIS source, so tables owned by
    # other sources (or hand-authored tables with no data_source_id) are left
    # untouched as a single global document region.
    owned: dict[str, dict] = {}
    for name, entry in (existing or {}).items():
        if isinstance(entry, dict) and entry.get("data_source_id") == data_source_id:
            owned[name] = entry

    merged: dict[str, object] = {}
    # Carry over every existing table that is NOT owned by this source verbatim.
    for name, entry in (existing or {}).items():
        if name not in owned:
            merged[name] = deepcopy(entry)

    for name, physical in (introspected or {}).items():
        physical = physical or {}
        old = owned.get(name)
        if old is None:
            # New table: physical facts become the placeholder; business fields
            # that a human owns (description / time_fields / relationships) stay
            # empty for the UI, but the plain physical column names are kept as a
            # placeholder so the editor is not blank.
            placeholder = deepcopy(physical)
            for field in _BUSINESS_FIELDS:
                if field == "columns":
                    continue
                placeholder.pop(field, None)
            merged[name] = placeholder
            warnings.append(f"新增表 {name}（物理结构已占位，请在 Semantic Studio 补充说明/时间字段/列说明）")
            continue

        older = deepcopy(old)
        # Overwrite physical facts only.
        for field in _PHYSICAL_FIELDS:
            if field in physical:
                older[field] = deepcopy(physical[field])
            else:
                older.pop(field, None)
        older["data_source_id"] = physical.get("data_source_id", data_source_id)
        older["dialect"] = physical.get("dialect", "oracle")
        older["source"] = "oracle_introspection"

        # MAIN_KEY: backfill only when the human left it empty.
        if not str(older.get("MAIN_KEY") or "").strip():
            pk = ",".join(physical.get("MAIN_KEY", "").split(",") if physical.get("MAIN_KEY") else [])
            if pk:
                older["MAIN_KEY"] = pk

        # columns: preserve every human-authored entry; only surface drift.
        if "columns" in physical:
            human_cols = list(older.get("columns") or [])
            physical_names = [_column_name(c) for c in (physical.get("columns") or [])]
            if human_cols:
                human_known = {_column_name(c) for c in human_cols}
                added = [n for n in physical_names if n and n not in human_known]
                if added:
                    warnings.append(
                        f"{name} 在 Oracle 中新增列：{'、'.join(added)}（请在 Semantic Studio 决定是否补充中文说明）"
                    )
                dropped = [c for c in human_cols if _column_name(c) and _column_name(c) not in set(physical_names)]
                for d in dropped:
                    warnings.append(f"{name} 的列 {d} 在 Oracle 中已不存在（人工列说明保留，未删除）")
            else:
                # Nobody authored column text yet: backfill physical names so
                # the UI is not empty, but these remain plain placeholders.
                older["columns"] = list(physical.get("columns") or [])

        # Physical foreign keys become suggestions, never business relationships.
        phys_rels = physical.get("relationships") or {}
        if phys_rels:
            warnings.append(
                f"{name} 检测到物理外键 {phys_rels}（业务 join 请在 join_patterns / 表 relationships 中人工确认，不自动写入）"
            )

        merged[name] = older

    # Tables that this source used to own but introspection no longer sees.
    for name in owned:
        if name not in introspected:
            # Preserve the human-authored asset verbatim; only surface drift.
            merged[name] = deepcopy(owned[name])
            warnings.append(f"{name} 未在本次同步中读到（可能已在 Oracle 中删除，保留人工资产，未自动删除）")

    return merged, warnings


class OracleSchemaIntrospector:
    """Reads Oracle physical metadata and converts it to semantic table assets."""

    def inspect(
        self,
        database_url: str,
        schemas: list[str] | None = None,
        *,
        include_views: bool = True,
    ) -> dict:
        connector = DatabaseConnector(database_url=database_url, sql_dialect="oracle")
        if connector.engine is None:
            raise RuntimeError("Oracle data source is not configured")

        inspector = inspect(connector.engine)
        selected_schemas = [item.strip().upper() for item in (schemas or []) if item.strip()]
        if not selected_schemas:
            selected_schemas = [str(inspector.default_schema_name or "").upper()]

        tables_metadata: dict[str, dict] = {}
        relationship_count = 0
        column_count = 0
        warnings: list[str] = []

        for schema in selected_schemas:
            try:
                object_names = list(inspector.get_table_names(schema=schema))
                if include_views:
                    object_names.extend(inspector.get_view_names(schema=schema))
            except Exception as exc:
                warnings.append(f"无法读取 Schema {schema}: {exc}")
                continue

            for table_name in sorted(set(object_names)):
                qualified_name = f"{schema}.{table_name}"
                columns = inspector.get_columns(table_name, schema=schema)
                primary_key = inspector.get_pk_constraint(table_name, schema=schema) or {}
                foreign_keys = inspector.get_foreign_keys(table_name, schema=schema) or []
                relationships: dict[str, str] = {}
                column_details: list[dict] = []

                for column in columns:
                    column_details.append(
                        {
                            "name": str(column["name"]),
                            "type": str(column.get("type", "")),
                            "nullable": bool(column.get("nullable", True)),
                            "default": None
                            if column.get("default") is None
                            else str(column.get("default")),
                            "comment": column.get("comment"),
                        }
                    )

                for foreign_key in foreign_keys:
                    local_columns = foreign_key.get("constrained_columns") or []
                    remote_columns = foreign_key.get("referred_columns") or []
                    remote_schema = foreign_key.get("referred_schema") or schema
                    remote_table = foreign_key.get("referred_table")
                    if not remote_table or len(local_columns) != len(remote_columns):
                        continue
                    for local_column, remote_column in zip(local_columns, remote_columns):
                        relationships[str(local_column)] = (
                            f"{remote_schema}.{remote_table}.{remote_column}"
                        )
                        relationship_count += 1

                comment = inspector.get_table_comment(table_name, schema=schema) or {}
                primary_columns = primary_key.get("constrained_columns") or []
                tables_metadata[qualified_name] = {
                    "schema": schema,
                    "table_name": table_name,
                    "description": comment.get("text") or "",
                    "columns": [str(column["name"]) for column in columns],
                    "column_details": column_details,
                    "MAIN_KEY": ",".join(primary_columns),
                    "relationships": relationships,
                    "source": "oracle_introspection",
                }
                column_count += len(columns)

        return {
            "tables_metadata": tables_metadata,
            "table_count": len(tables_metadata),
            "column_count": column_count,
            "relationship_count": relationship_count,
            "warnings": warnings,
        }
