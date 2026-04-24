from __future__ import annotations

import re
from pathlib import Path

from backend.app.models.admin import (
    SemanticViewBootstrapResponse,
    SemanticViewDependencyRecord,
    SemanticViewDraftCollectionResponse,
    SemanticViewDraftRecord,
    SemanticViewValidationResponse,
)


class SemanticViewService:
    def __init__(self, semantic_layer: dict, drafts_path: Path, database_connector=None) -> None:
        self.semantic_layer = semantic_layer
        self.drafts_path = drafts_path
        self.database_connector = database_connector

    def list_drafts(self) -> SemanticViewDraftCollectionResponse:
        drafts = self._parse_view_drafts()
        records = [self._build_draft_record(name, sql) for name, sql in drafts.items()]
        return SemanticViewDraftCollectionResponse(views=records, count=len(records))

    def validate_view(self, view_name: str) -> SemanticViewValidationResponse:
        drafts = self._parse_view_drafts()
        sql = drafts.get(view_name)
        if not sql:
            raise KeyError(view_name)

        record = self._build_draft_record(view_name, sql)
        dependencies = self._collect_dependencies(view_name, drafts=drafts)
        warnings = self._build_validation_warnings(record, dependencies)
        database_connected = bool(self.database_connector and self.database_connector.connected)
        bootstrap_ready = (
            database_connected
            and record.contract_aligned
            and all(dep.exists_in_database is not False for dep in dependencies)
        )
        return SemanticViewValidationResponse(
            view=record,
            dependencies=dependencies,
            warnings=warnings,
            database_connected=database_connected,
            bootstrap_ready=bootstrap_ready,
        )

    def bootstrap_view(self, view_name: str) -> SemanticViewBootstrapResponse:
        validation = self.validate_view(view_name)
        if self.database_connector is None:
            return SemanticViewBootstrapResponse(
                view=view_name,
                contract_aligned=validation.view.contract_aligned,
                database_connected=False,
                bootstrap_ready=False,
                dependencies=validation.dependencies,
                warnings=validation.warnings,
                executed=False,
                error="database connector is not configured",
            )

        result = self.database_connector.execute_script(validation.view.sql)
        return SemanticViewBootstrapResponse(
            view=view_name,
            contract_aligned=validation.view.contract_aligned,
            database_connected=validation.database_connected,
            bootstrap_ready=validation.bootstrap_ready,
            dependencies=validation.dependencies,
            warnings=validation.warnings,
            executed=bool(result.get("executed")),
            statements=result.get("statements"),
            error=result.get("error"),
        )

    def _build_draft_record(self, name: str, sql: str) -> SemanticViewDraftRecord:
        semantic_view = self._semantic_views().get(name, {})
        declared_fields = self._extract_select_aliases(sql)
        semantic_fields = list(semantic_view.get("output_fields", []))
        missing_fields = [field for field in semantic_fields if field not in declared_fields]
        extra_fields = [field for field in declared_fields if field not in semantic_fields]
        return SemanticViewDraftRecord(
            name=name,
            sql=sql,
            declared_output_fields=declared_fields,
            semantic_output_fields=semantic_fields,
            semantic_status=semantic_view.get("status"),
            semantic_stage=semantic_view.get("implementation_stage"),
            missing_fields=missing_fields,
            extra_fields=extra_fields,
            contract_aligned=not missing_fields and not extra_fields and bool(semantic_fields),
        )

    def _semantic_views(self) -> dict[str, dict]:
        return {
            item["name"]: item
            for item in self.semantic_layer.get("semantic_views", [])
        }

    def _collect_dependencies(
        self,
        view_name: str,
        drafts: dict[str, str] | None = None,
    ) -> list[SemanticViewDependencyRecord]:
        semantic_view = self._semantic_views().get(view_name, {})
        source_tables = list(semantic_view.get("source_tables", []))
        draft_names = set(drafts or self._parse_view_drafts())
        dependencies: list[SemanticViewDependencyRecord] = []
        for name in source_tables:
            exists_in_drafts = name in draft_names
            dependency_type = "semantic_view" if exists_in_drafts else "table"
            exists_in_database: bool | None = None
            database_status: str | None = None
            database_warning: str | None = None
            if self.database_connector and self.database_connector.connected:
                probe = self.database_connector.execute_readonly(f"SELECT 1 FROM {name} LIMIT 1")
                exists_in_database = bool(probe.executed)
                database_status = probe.status
                if not probe.executed:
                    database_warning = "; ".join(probe.errors) or "dependency probe failed"
                elif probe.warnings:
                    database_warning = "; ".join(probe.warnings)
            dependencies.append(
                SemanticViewDependencyRecord(
                    name=name,
                    dependency_type=dependency_type,
                    exists_in_drafts=exists_in_drafts,
                    exists_in_database=exists_in_database,
                    database_status=database_status,
                    database_warning=database_warning,
                )
            )
        return dependencies

    def _build_validation_warnings(
        self,
        record: SemanticViewDraftRecord,
        dependencies: list[SemanticViewDependencyRecord],
    ) -> list[str]:
        warnings: list[str] = []
        if not record.contract_aligned:
            warnings.append("semantic contract is not aligned with declared output fields")
        if record.semantic_stage == "logical_scaffold":
            warnings.append("view is still in logical_scaffold stage and may need real-data refinement")
        missing_dependencies = [dep.name for dep in dependencies if dep.exists_in_database is False]
        if missing_dependencies:
            warnings.append(
                f"database dependencies not found or not queryable: {', '.join(missing_dependencies)}"
            )
        if not (self.database_connector and self.database_connector.connected):
            warnings.append("database dependency checks were skipped because database is not connected")
        return warnings

    def _parse_view_drafts(self) -> dict[str, str]:
        content = self.drafts_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+(.*?);(?=\s*(?:--\s+semantic_|$))",
            re.IGNORECASE | re.DOTALL,
        )
        drafts: dict[str, str] = {}
        for match in pattern.finditer(content):
            view_name = match.group(1)
            sql = f"CREATE OR REPLACE VIEW {view_name} AS\n{match.group(2).strip()};"
            drafts[view_name] = sql
        return drafts

    def _extract_select_aliases(self, sql: str) -> list[str]:
        first_select = re.search(r"SELECT\s+(.*?)\s+FROM\s", sql, re.IGNORECASE | re.DOTALL)
        if not first_select:
            return []
        fields = []
        for part in first_select.group(1).split(","):
            alias_match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", part, re.IGNORECASE)
            if alias_match:
                fields.append(alias_match.group(1))
                continue
            tail = part.strip().split(".")[-1].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tail):
                fields.append(tail)
        deduped: list[str] = []
        for field in fields:
            if field not in deduped:
                deduped.append(field)
        return deduped
