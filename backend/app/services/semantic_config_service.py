from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Callable

from backend.app.models.semantic_config import (
    DatabaseStatusRecord,
    PhysicalCatalogRecord,
    SemanticAssetDraftRecord,
    SemanticReleaseDetailRecord,
)
from backend.app.models.example_library import ExampleTemplateRecord
from backend.app.repositories.db_semantic_config_repository import DbSemanticConfigRepository
from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.metadata_registry import ASSET_NAMES, MetadataRegistry
from backend.app.services.oracle_schema_introspector import (
    OracleSchemaIntrospector,
    merge_tables_metadata,
)
from backend.app.services.sql_ast_validator import SqlAstValidator


SEMANTIC_ASSET_DEFAULTS: dict[str, object] = {
    "tables_metadata": {},
    "business_knowledge": {"entries": []},
    "join_patterns": {"patterns": []},
    "examples_template": [],
}


class SemanticConfigService:
    """Own the single-database catalog -> draft -> release control plane."""

    def __init__(
        self,
        *,
        repository: DbSemanticConfigRepository,
        business_connector: DatabaseConnector,
        schema_scope: list[str],
        metadata_registry: MetadataRegistry,
        introspector: OracleSchemaIntrospector | None = None,
        release_prepare: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.repository = repository
        self.business_connector = business_connector
        self.schema_scope = list(schema_scope)
        self.metadata_registry = metadata_registry
        self.introspector = introspector or OracleSchemaIntrospector()
        self.release_prepare = release_prepare
        self.sql_inspector = SqlAstValidator()

    def database_status(self) -> DatabaseStatusRecord:
        health = self.business_connector.test_connection()
        catalog = self.repository.get_catalog()
        counts = self._catalog_counts(catalog)
        return DatabaseStatusRecord(
            configured=self.business_connector.connected,
            connected=bool(health.get("connected")),
            dialect="oracle",
            schema_scope=list(catalog.schema_scope if catalog else self.schema_scope),
            sync_status=catalog.status if catalog else "not_synced",
            table_count=counts[0],
            column_count=counts[1],
            relationship_count=counts[2],
            catalog_hash=catalog.catalog_hash if catalog else None,
            active_release_id=self.repository.get_active_release_id(),
            last_sync_at=catalog.synced_at if catalog else None,
            last_error=(catalog.last_error if catalog else None) or health.get("error"),
        )

    def get_catalog(self) -> PhysicalCatalogRecord | None:
        return self.repository.get_catalog()

    def sync_catalog(self, *, include_views: bool = True) -> tuple[PhysicalCatalogRecord, SemanticAssetDraftRecord, list[str]]:
        schema_scope = list(self.schema_scope)
        self.repository.mark_catalog_syncing(schema_scope)
        try:
            introspection = self.introspector.inspect_connector(
                self.business_connector,
                schema_scope,
                include_views=include_views,
            )
            catalog_payload = {
                "tables_metadata": introspection.get("tables_metadata", {}),
                "table_count": int(introspection.get("table_count") or 0),
                "column_count": int(introspection.get("column_count") or 0),
                "relationship_count": int(introspection.get("relationship_count") or 0),
            }
            catalog_hash = self._content_hash(catalog_payload)
            catalog_warnings = list(introspection.get("warnings") or [])
            catalog = self.repository.save_catalog(
                schema_scope=schema_scope,
                catalog=catalog_payload,
                catalog_hash=catalog_hash,
                warnings=catalog_warnings,
            )
            current = self.repository.get_draft("tables_metadata")
            current_tables = current.content if current and isinstance(current.content, dict) else {}
            merged, merge_warnings = merge_tables_metadata(
                current_tables,
                catalog_payload["tables_metadata"],
            )
            draft = self.repository.save_draft(
                name="tables_metadata",
                content=merged,
                expected_version=current.version if current else None,
            )
            self.ensure_drafts()
            return catalog, draft, catalog_warnings + merge_warnings
        except Exception as exc:
            self.repository.mark_catalog_error(schema_scope, str(exc))
            raise

    def ensure_drafts(self) -> list[SemanticAssetDraftRecord]:
        current = {item.name: item for item in self.repository.list_drafts()}
        # Drafts are created only after a physical catalog exists. Before the
        # first schema sync the workspace is intentionally empty, so local
        # JSON fixtures can never become runtime configuration by accident.
        catalog = self.repository.get_catalog()
        if catalog is None or catalog.status != "ready" or not catalog.catalog_hash:
            return [current[name] for name in ASSET_NAMES if name in current]
        for name in ASSET_NAMES:
            if name in current:
                continue
            current[name] = self.repository.save_draft(
                name=name,
                content=deepcopy(SEMANTIC_ASSET_DEFAULTS[name]),
            )
        return [current[name] for name in ASSET_NAMES]

    def save_draft(
        self,
        *,
        name: str,
        content: dict | list | str,
        expected_version: int | None,
    ) -> tuple[SemanticAssetDraftRecord, list[str]]:
        if name not in ASSET_NAMES:
            raise ValueError(f"unsupported semantic asset: {name}")
        catalog = self._require_catalog()
        errors, warnings = self._validate_asset(name, content, catalog)
        if errors:
            raise ValueError("; ".join(errors))
        draft = self.repository.save_draft(
            name=name,
            content=content,
            expected_version=expected_version,
        )
        return draft, warnings

    def add_example_to_draft(
        self,
        example: ExampleTemplateRecord,
    ) -> tuple[SemanticAssetDraftRecord, list[str]]:
        self._require_catalog()
        drafts = {item.name: item for item in self.ensure_drafts()}
        current = drafts["examples_template"]
        examples = list(current.content) if isinstance(current.content, list) else []
        example_id = example.id
        if example_id and any(
            isinstance(item, dict) and item.get("id") == example_id
            for item in examples
        ):
            raise ValueError(f"example id already exists: {example_id}")
        examples.append(example.model_dump(mode="json", exclude_none=True))
        return self.save_draft(
            name="examples_template",
            content=examples,
            expected_version=current.version,
        )

    def publish(self, *, created_by: str | None) -> tuple[SemanticReleaseDetailRecord, list[str]]:
        catalog = self._require_catalog()
        drafts = {item.name: item for item in self.ensure_drafts()}
        snapshot: dict[str, object] = {}
        errors: list[str] = []
        warnings: list[str] = []
        for name in ASSET_NAMES:
            content = drafts[name].content
            asset_errors, asset_warnings = self._validate_asset(name, content, catalog)
            errors.extend(f"{name}: {error}" for error in asset_errors)
            warnings.extend(f"{name}: {warning}" for warning in asset_warnings)
            snapshot[name] = deepcopy(content)
        errors.extend(self._validate_cross_asset_references(snapshot))
        if errors:
            raise ValueError("; ".join(errors))
        draft_versions = {name: drafts[name].version for name in ASSET_NAMES}
        snapshot["release_metadata"] = {
            "asset_schema_version": 1,
            "catalog_hash": catalog.catalog_hash,
            "draft_versions": draft_versions,
        }
        if not catalog.catalog_hash:
            raise ValueError("physical catalog hash is missing")
        release = self.repository.create_release(
            snapshot=snapshot,
            catalog_hash=catalog.catalog_hash,
            draft_versions=draft_versions,
            created_by=created_by,
        )
        try:
            if self.release_prepare is not None:
                self.release_prepare(release.id, snapshot)
            release = self.repository.activate_release(release.id)
            return release, warnings
        except Exception as exc:
            self.repository.fail_release(release.id, str(exc))
            raise

    def _require_catalog(self) -> PhysicalCatalogRecord:
        catalog = self.repository.get_catalog()
        if catalog is None or catalog.status != "ready" or not catalog.catalog_hash:
            raise ValueError("sync the Oracle schema before editing or publishing semantic assets")
        return catalog

    def _validate_asset(
        self,
        name: str,
        content: object,
        catalog: PhysicalCatalogRecord,
    ) -> tuple[list[str], list[str]]:
        try:
            self.metadata_registry.validate(name, content)
        except (TypeError, ValueError) as exc:
            return [str(exc)], []
        if name == "tables_metadata":
            return self._validate_tables(content, catalog)
        if name == "examples_template":
            errors: list[str] = []
            for index, example in enumerate(content if isinstance(content, list) else []):
                if not isinstance(example, dict):
                    errors.append(f"example {index + 1} must be an object")
                    continue
                sql = str(example.get("sql") or "").strip()
                inspection = self.sql_inspector.inspect(sql)
                sql_errors, _ = self.sql_inspector.validate(sql, inspection)
                errors.extend(f"example {index + 1}: {error}" for error in sql_errors)
            return errors, []
        return [], []

    def _validate_tables(
        self,
        content: object,
        catalog: PhysicalCatalogRecord,
    ) -> tuple[list[str], list[str]]:
        if not isinstance(content, dict):
            return ["tables_metadata must be an object"], []
        physical_tables = catalog.catalog.get("tables_metadata", {})
        if not isinstance(physical_tables, dict):
            return ["physical catalog tables_metadata is invalid"], []
        errors: list[str] = []
        warnings: list[str] = []
        for table_name, table in content.items():
            if table_name not in physical_tables:
                errors.append(f"table is not present in the physical catalog: {table_name}")
                continue
            if not isinstance(table, dict):
                errors.append(f"table metadata must be an object: {table_name}")
                continue
            physical_columns = self._physical_columns(physical_tables[table_name])
            for column in table.get("columns", []) or []:
                if self._column_name(column) not in physical_columns:
                    errors.append(f"unknown column {column} in {table_name}")
            for source_column, raw_target in (table.get("relationships") or {}).items():
                if str(source_column).upper() not in physical_columns:
                    errors.append(f"unknown relationship column {table_name}.{source_column}")
                target_table, target_column = self._split_relationship_target(
                    str(raw_target or ""),
                    set(physical_tables),
                )
                if not target_table or not target_column:
                    errors.append(f"relationship target must be table.column: {raw_target}")
                    continue
                target_physical = physical_tables.get(target_table)
                if not isinstance(target_physical, dict):
                    errors.append(f"relationship target table is unknown: {target_table}")
                    continue
                if target_column.upper() not in self._physical_columns(target_physical):
                    errors.append(
                        f"relationship target column is unknown: {target_table}.{target_column}"
                    )
        missing = sorted(set(physical_tables) - set(content))
        if missing:
            warnings.append(f"{len(missing)} physical tables are not included in the semantic draft")
        return errors, warnings

    def _validate_cross_asset_references(self, snapshot: dict[str, object]) -> list[str]:
        tables = snapshot.get("tables_metadata", {})
        known_tables = set(tables) if isinstance(tables, dict) else set()
        errors: list[str] = []
        knowledge = snapshot.get("business_knowledge", {})
        if isinstance(knowledge, dict):
            for entry in knowledge.get("entries", []):
                if isinstance(entry, dict):
                    errors.extend(self._unknown_table_errors("business knowledge", entry, known_tables))
        joins = snapshot.get("join_patterns", {})
        if isinstance(joins, dict):
            for entry in joins.get("patterns", []):
                if isinstance(entry, dict):
                    errors.extend(self._unknown_table_errors("join pattern", entry, known_tables))
        examples = snapshot.get("examples_template", [])
        if isinstance(examples, list):
            known_lower = {table.lower() for table in known_tables}
            known_bare = {table.rsplit(".", 1)[-1].lower() for table in known_tables}
            for index, example in enumerate(examples):
                if not isinstance(example, dict):
                    continue
                inspection = self.sql_inspector.inspect(str(example.get("sql") or ""))
                ctes = {item.lower() for item in inspection.cte_names}
                for source in inspection.sources:
                    normalized = source.strip('"').lower()
                    if normalized in ctes:
                        continue
                    if normalized not in known_lower and normalized.rsplit(".", 1)[-1] not in known_bare:
                        errors.append(f"example {index + 1} references unknown table: {source}")
        return errors

    @staticmethod
    def _unknown_table_errors(label: str, entry: dict, known_tables: set[str]) -> list[str]:
        return [
            f"{label} references unknown table: {table_name}"
            for table_name in entry.get("tables", []) or []
            if str(table_name) not in known_tables
        ]

    @staticmethod
    def _catalog_counts(catalog: PhysicalCatalogRecord | None) -> tuple[int, int, int]:
        if catalog is None:
            return 0, 0, 0
        return (
            int(catalog.catalog.get("table_count") or 0),
            int(catalog.catalog.get("column_count") or 0),
            int(catalog.catalog.get("relationship_count") or 0),
        )

    @staticmethod
    def _physical_columns(table: object) -> set[str]:
        if not isinstance(table, dict):
            return set()
        details = {
            str(item.get("name") or "").upper()
            for item in table.get("column_details", [])
            if isinstance(item, dict)
        }
        return details or {
            SemanticConfigService._column_name(item)
            for item in table.get("columns", []) or []
        }

    @staticmethod
    def _column_name(value: object) -> str:
        text = str(value or "").strip()
        for marker in (" (", "("):
            if marker in text:
                text = text.split(marker, 1)[0]
                break
        return text.strip().upper()

    @staticmethod
    def _split_relationship_target(value: str, known_tables: set[str]) -> tuple[str | None, str | None]:
        target = value.strip()
        for table_name in sorted(known_tables, key=len, reverse=True):
            prefix = f"{table_name}."
            if target.startswith(prefix):
                return table_name, target[len(prefix) :]
        if "." not in target:
            return None, None
        table_name, column_name = target.rsplit(".", 1)
        return table_name, column_name

    @staticmethod
    def _content_hash(payload: object) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
