from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from backend.app.models.semantic_config import (
    PhysicalCatalogRecord,
    SemanticAssetDraftRecord,
    SemanticReleaseDetailRecord,
    SemanticReleaseRecord,
)
from backend.app.repositories.db_repository_utils import as_datetime, json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


CATALOG_KEY = "current"
RELEASE_STATE_KEY = "current"


class DbSemanticConfigRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def get_catalog(self) -> PhysicalCatalogRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT schema_scope_json, status, catalog_json, catalog_hash, warnings_json,
                   last_error, synced_at, updated_at
            FROM physical_catalogs WHERE catalog_key = :catalog_key
            """,
            {"catalog_key": CATALOG_KEY},
        )
        return self._catalog(row) if row else None

    def mark_catalog_syncing(self, schema_scope: list[str]) -> PhysicalCatalogRecord:
        return self._upsert_catalog_state(
            schema_scope=schema_scope,
            status="syncing",
            catalog=None,
            catalog_hash=None,
            warnings=[],
            last_error=None,
            synced_at=None,
        )

    def mark_catalog_error(self, schema_scope: list[str], error: str) -> PhysicalCatalogRecord:
        return self._upsert_catalog_state(
            schema_scope=schema_scope,
            status="error",
            catalog=None,
            catalog_hash=None,
            warnings=[],
            last_error=error,
            synced_at=None,
        )

    def save_catalog(
        self,
        *,
        schema_scope: list[str],
        catalog: dict,
        catalog_hash: str,
        warnings: list[str],
    ) -> PhysicalCatalogRecord:
        now = datetime.now(tz=timezone.utc)
        return self._upsert_catalog_state(
            schema_scope=schema_scope,
            status="ready",
            catalog=catalog,
            catalog_hash=catalog_hash,
            warnings=warnings,
            last_error=None,
            synced_at=now,
        )

    def _upsert_catalog_state(
        self,
        *,
        schema_scope: list[str],
        status: str,
        catalog: dict | None,
        catalog_hash: str | None,
        warnings: list[str],
        last_error: str | None,
        synced_at: datetime | None,
    ) -> PhysicalCatalogRecord:
        now = datetime.now(tz=timezone.utc)
        current = self.get_catalog()
        preserved_catalog = catalog if catalog is not None else (current.catalog if current else {})
        preserved_hash = catalog_hash if catalog_hash is not None else (current.catalog_hash if current else None)
        preserved_synced_at = synced_at if synced_at is not None else (current.synced_at if current else None)
        params = {
            "catalog_key": CATALOG_KEY,
            "schema_scope_json": json_dumps(schema_scope),
            "status": status,
            "catalog_json": json_dumps(preserved_catalog),
            "catalog_hash": preserved_hash,
            "warnings_json": json_dumps(warnings),
            "last_error": last_error,
            "synced_at": preserved_synced_at,
            "updated_at": now,
        }
        updated = self.database_connector.execute_write(
            """
            UPDATE physical_catalogs
            SET schema_scope_json = :schema_scope_json, status = :status,
                catalog_json = :catalog_json, catalog_hash = :catalog_hash,
                warnings_json = :warnings_json, last_error = :last_error,
                synced_at = :synced_at, updated_at = :updated_at
            WHERE catalog_key = :catalog_key
            """,
            params,
        )
        if updated == 0:
            self.database_connector.execute_write(
                """
                INSERT INTO physical_catalogs (
                    catalog_key, schema_scope_json, status, catalog_json, catalog_hash,
                    warnings_json, last_error, synced_at, updated_at
                ) VALUES (
                    :catalog_key, :schema_scope_json, :status, :catalog_json, :catalog_hash,
                    :warnings_json, :last_error, :synced_at, :updated_at
                )
                """,
                params,
            )
        saved = self.get_catalog()
        if saved is None:
            raise RuntimeError("failed to persist physical catalog")
        return saved

    def list_drafts(self) -> list[SemanticAssetDraftRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT name, content_json, version, updated_at
            FROM semantic_asset_drafts ORDER BY name
            """
        )
        return [self._draft(row) for row in rows]

    def get_draft(self, name: str) -> SemanticAssetDraftRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT name, content_json, version, updated_at
            FROM semantic_asset_drafts WHERE name = :name
            """,
            {"name": name},
        )
        return self._draft(row) if row else None

    def save_draft(
        self,
        *,
        name: str,
        content: dict | list | str,
        expected_version: int | None = None,
    ) -> SemanticAssetDraftRecord:
        now = datetime.now(tz=timezone.utc)
        current = self.get_draft(name)
        if current is None:
            if expected_version is not None:
                raise ValueError(
                    f"draft version conflict: expected {expected_version}, draft does not exist"
                )
            self.database_connector.execute_write(
                """
                INSERT INTO semantic_asset_drafts (name, content_json, version, updated_at)
                VALUES (:name, :content_json, 1, :updated_at)
                """,
                {"name": name, "content_json": json_dumps(content), "updated_at": now},
            )
        else:
            if expected_version is not None and current.version != expected_version:
                raise ValueError(
                    f"draft version conflict: expected {expected_version}, current {current.version}"
                )
            updated = self.database_connector.execute_write(
                """
                UPDATE semantic_asset_drafts
                SET content_json = :content_json, version = version + 1, updated_at = :updated_at
                WHERE name = :name AND version = :current_version
                """,
                {
                    "name": name,
                    "content_json": json_dumps(content),
                    "current_version": current.version,
                    "updated_at": now,
                },
            )
            if updated != 1:
                raise ValueError("draft version conflict: draft changed while saving")
        saved = self.get_draft(name)
        if saved is None:
            raise RuntimeError("failed to persist semantic draft")
        return saved

    def get_active_release_id(self) -> str | None:
        row = self.database_connector.fetch_one(
            "SELECT active_release_id FROM semantic_release_state WHERE state_key = :state_key",
            {"state_key": RELEASE_STATE_KEY},
        )
        return str(row["active_release_id"]) if row and row.get("active_release_id") else None

    def list_releases(self) -> list[SemanticReleaseRecord]:
        rows = self.database_connector.fetch_all(
            """
            SELECT id, version, status, catalog_hash, created_by, error, created_at, activated_at
            FROM semantic_releases ORDER BY version DESC
            """
        )
        return [self._release(row) for row in rows]

    def get_release(self, release_id: str) -> SemanticReleaseDetailRecord | None:
        row = self.database_connector.fetch_one(
            """
            SELECT id, version, status, snapshot_json, catalog_hash, draft_versions_json,
                   created_by, error, created_at, activated_at
            FROM semantic_releases WHERE id = :id
            """,
            {"id": release_id},
        )
        if row is None:
            return None
        return SemanticReleaseDetailRecord(
            **self._release(row).model_dump(),
            snapshot=json_loads(row.get("snapshot_json"), {}),
            draft_versions=json_loads(row.get("draft_versions_json"), {}),
        )

    def create_release(
        self,
        *,
        snapshot: dict,
        catalog_hash: str,
        draft_versions: dict[str, int],
        created_by: str | None,
    ) -> SemanticReleaseDetailRecord:
        release_id = f"rel_{uuid4().hex[:20]}"
        now = datetime.now(tz=timezone.utc)
        with self.database_connector.begin() as connection:
            lock_clause = "" if self.database_connector.sql_dialect.name == "sqlite" else " FOR UPDATE"
            version_row = connection.execute(
                text(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM semantic_releases"
                    f"{lock_clause}"
                )
            ).mappings().first()
            version = int(version_row["version"] if version_row else 0) + 1
            connection.execute(
                text(
                    """
                    INSERT INTO semantic_releases (
                        id, version, status, snapshot_json, catalog_hash, draft_versions_json,
                        created_by, error, created_at, activated_at
                    ) VALUES (
                        :id, :version, 'building', :snapshot_json, :catalog_hash,
                        :draft_versions_json, :created_by, NULL, :created_at, NULL
                    )
                    """
                ),
                {
                    "id": release_id,
                    "version": version,
                    "snapshot_json": json_dumps(snapshot),
                    "catalog_hash": catalog_hash,
                    "draft_versions_json": json_dumps(draft_versions),
                    "created_by": created_by,
                    "created_at": now,
                },
            )
        release = self.get_release(release_id)
        if release is None:
            raise RuntimeError("failed to persist semantic release")
        return release

    def activate_release(self, release_id: str) -> SemanticReleaseDetailRecord:
        now = datetime.now(tz=timezone.utc)
        with self.database_connector.begin() as connection:
            lock_clause = "" if self.database_connector.sql_dialect.name == "sqlite" else " FOR UPDATE"
            candidate = connection.execute(
                text(
                    "SELECT id, status FROM semantic_releases WHERE id = :id"
                    f"{lock_clause}"
                ),
                {"id": release_id},
            ).mappings().first()
            if candidate is None:
                raise ValueError(f"semantic release not found: {release_id}")
            if str(candidate["status"]) != "building":
                raise ValueError(f"semantic release is not building: {release_id}")
            connection.execute(
                text("UPDATE semantic_releases SET status = 'inactive' WHERE status = 'active'")
            )
            connection.execute(
                text(
                    """
                    UPDATE semantic_releases
                    SET status = 'active', activated_at = :activated_at
                    WHERE id = :id
                    """
                ),
                {"id": release_id, "activated_at": now},
            )
            state_params = {
                "state_key": RELEASE_STATE_KEY,
                "active_release_id": release_id,
                "updated_at": now,
            }
            updated = connection.execute(
                text(
                    """
                    UPDATE semantic_release_state
                    SET active_release_id = :active_release_id, updated_at = :updated_at
                    WHERE state_key = :state_key
                    """
                ),
                state_params,
            )
            if int(updated.rowcount or 0) == 0:
                connection.execute(
                    text(
                        """
                        INSERT INTO semantic_release_state (state_key, active_release_id, updated_at)
                        VALUES (:state_key, :active_release_id, :updated_at)
                        """
                    ),
                    state_params,
                )
        release = self.get_release(release_id)
        if release is None:
            raise RuntimeError("failed to activate semantic release")
        return release

    def fail_release(self, release_id: str, error: str) -> SemanticReleaseDetailRecord:
        updated = self.database_connector.execute_write(
            """
            UPDATE semantic_releases
            SET status = 'failed', error = :error
            WHERE id = :id AND status = 'building'
            """,
            {"id": release_id, "error": error},
        )
        if updated != 1:
            raise ValueError(f"semantic release cannot be failed: {release_id}")
        release = self.get_release(release_id)
        if release is None:
            raise RuntimeError("failed to load failed semantic release")
        return release

    @staticmethod
    def _catalog(row: dict) -> PhysicalCatalogRecord:
        return PhysicalCatalogRecord(
            schema_scope=json_loads(row.get("schema_scope_json"), []),
            status=str(row.get("status") or "not_synced"),
            catalog=json_loads(row.get("catalog_json"), {}),
            catalog_hash=row.get("catalog_hash"),
            warnings=json_loads(row.get("warnings_json"), []),
            last_error=row.get("last_error"),
            synced_at=as_datetime(row["synced_at"]) if row.get("synced_at") else None,
            updated_at=as_datetime(row["updated_at"]),
        )

    @staticmethod
    def _draft(row: dict) -> SemanticAssetDraftRecord:
        return SemanticAssetDraftRecord(
            name=str(row["name"]),
            content=json_loads(row.get("content_json"), {}),
            version=int(row["version"]),
            updated_at=as_datetime(row["updated_at"]),
        )

    @staticmethod
    def _release(row: dict) -> SemanticReleaseRecord:
        return SemanticReleaseRecord(
            id=str(row["id"]),
            version=int(row["version"]),
            status=str(row["status"]),
            catalog_hash=str(row["catalog_hash"]),
            created_by=row.get("created_by"),
            error=row.get("error"),
            created_at=as_datetime(row["created_at"]),
            activated_at=as_datetime(row["activated_at"]) if row.get("activated_at") else None,
        )
