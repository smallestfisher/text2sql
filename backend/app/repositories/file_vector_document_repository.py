from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
from uuid import uuid4

from backend.app.repositories.db_repository_utils import as_datetime


class FileVectorDocumentRepository:
    """Rebuildable vector cache stored outside the runtime database."""

    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path
        self.root_path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def find_by_document_ids(self, scope_id: str, document_ids: list[str]) -> list[dict]:
        if not document_ids:
            return []
        with self._lock:
            documents = self._read_scope(scope_id)
        return [documents[document_id] for document_id in document_ids if document_id in documents]

    def upsert_documents(self, documents: list[dict]) -> int:
        if not documents:
            return 0
        by_scope: dict[str, list[dict]] = {}
        for document in documents:
            by_scope.setdefault(str(document["scope_id"]), []).append(document)
        with self._lock:
            for scope_id, scoped_documents in by_scope.items():
                current = self._read_scope(scope_id)
                for document in scoped_documents:
                    current[str(document["document_id"])] = dict(document)
                self._write_scope(scope_id, current)
        return len(documents)

    def delete_missing(self, scope_id: str, document_ids: list[str]) -> int:
        keep = set(document_ids)
        with self._lock:
            current = self._read_scope(scope_id)
            retained = {
                document_id: document
                for document_id, document in current.items()
                if document_id in keep
            }
            deleted_count = len(current) - len(retained)
            if deleted_count:
                self._write_scope(scope_id, retained)
        return deleted_count

    def _read_scope(self, scope_id: str) -> dict[str, dict]:
        path = self._scope_path(scope_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"failed to read vector cache {path}: {exc}") from exc
        if payload.get("scope_id") != scope_id or not isinstance(payload.get("documents"), dict):
            raise RuntimeError(f"invalid vector cache payload: {path}")
        return {
            str(document_id): self._deserialize_document(document)
            for document_id, document in payload["documents"].items()
            if isinstance(document, dict)
        }

    def _write_scope(self, scope_id: str, documents: dict[str, dict]) -> None:
        path = self._scope_path(scope_id)
        if not documents:
            path.unlink(missing_ok=True)
            return
        payload = {
            "scope_id": scope_id,
            "documents": {
                document_id: self._serialize_document(document)
                for document_id, document in documents.items()
            },
        }
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _scope_path(self, scope_id: str) -> Path:
        digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()
        return self.root_path / f"{digest}.json"

    @staticmethod
    def _serialize_document(document: dict) -> dict:
        result = dict(document)
        for field in ("created_at", "updated_at"):
            value = result.get(field)
            if isinstance(value, datetime):
                result[field] = value.isoformat()
        return result

    @staticmethod
    def _deserialize_document(document: dict) -> dict:
        result = dict(document)
        for field in ("created_at", "updated_at"):
            value = result.get(field)
            if isinstance(value, str):
                result[field] = as_datetime(value)
        return result
