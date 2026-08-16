from __future__ import annotations

from typing import Protocol


class VectorDocumentRepository(Protocol):
    def find_by_document_ids(self, scope_id: str, document_ids: list[str]) -> list[dict]: ...

    def upsert_documents(self, documents: list[dict]) -> int: ...

    def delete_missing(self, scope_id: str, document_ids: list[str]) -> int: ...
