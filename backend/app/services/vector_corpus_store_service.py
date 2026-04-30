from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging

from backend.app.repositories.db_vector_document_repository import DbVectorDocumentRepository
from backend.app.services.vector_retriever import VectorRetriever


logger = logging.getLogger(__name__)


@dataclass
class VectorCorpusSyncResult:
    documents: list[dict]
    persisted_document_count: int
    reused_document_count: int
    rebuilt_document_count: int
    deleted_document_count: int
    upserted_document_count: int
    vector_sync_last_updated_at: str
    embedding_signature: dict | None

    def summary(self) -> dict:
        return {
            "persisted_document_count": self.persisted_document_count,
            "reused_document_count": self.reused_document_count,
            "rebuilt_document_count": self.rebuilt_document_count,
            "deleted_document_count": self.deleted_document_count,
            "upserted_document_count": self.upserted_document_count,
            "vector_sync_last_updated_at": self.vector_sync_last_updated_at,
            "embedding_signature": self.embedding_signature,
        }


class VectorCorpusStoreService:
    def __init__(
        self,
        repository: DbVectorDocumentRepository,
        vector_retriever: VectorRetriever,
    ) -> None:
        self.repository = repository
        self.vector_retriever = vector_retriever

    def sync(self, corpus_documents: list[dict]) -> VectorCorpusSyncResult:
        now = datetime.now(tz=timezone.utc)
        if not self.vector_retriever.enabled:
            return VectorCorpusSyncResult(
                documents=[],
                persisted_document_count=0,
                reused_document_count=0,
                rebuilt_document_count=0,
                deleted_document_count=0,
                upserted_document_count=0,
                vector_sync_last_updated_at=now.isoformat(),
                embedding_signature=None,
            )

        existing_rows = {
            item["document_id"]: item
            for item in self.repository.find_by_document_ids(
                [self._document_id(document["source_type"], document["source_id"]) for document in corpus_documents]
            )
        }

        configured_signature = self.vector_retriever.embedding_signature()
        active_signature = configured_signature
        for _ in range(2):
            if active_signature is None:
                break
            sync_result = self._sync_with_signature(
                corpus_documents=corpus_documents,
                existing_rows=existing_rows,
                active_signature=active_signature,
                now=now,
            )
            restart_signature = sync_result.pop("restart_signature", None)
            if restart_signature is None:
                result = VectorCorpusSyncResult(**sync_result)
                logger.info(
                    "vector corpus sync finished: total=%s reused=%s rebuilt=%s deleted=%s upserted=%s backend=%s",
                    result.persisted_document_count,
                    result.reused_document_count,
                    result.rebuilt_document_count,
                    result.deleted_document_count,
                    result.upserted_document_count,
                    result.embedding_signature.get("embedding_backend") if result.embedding_signature else None,
                )
                return result
            active_signature = restart_signature

        raise RuntimeError("vector corpus sync could not stabilize embedding backend")

    def _sync_with_signature(
        self,
        corpus_documents: list[dict],
        existing_rows: dict[str, dict],
        active_signature: dict,
        now: datetime,
    ) -> dict:
        prepared_documents: list[dict] = []
        rows_to_upsert: list[dict] = []
        reused_count = 0
        rebuilt_count = 0
        document_ids: list[str] = []

        for document in corpus_documents:
            candidate = self._build_candidate(document=document, signature=active_signature, now=now)
            document_ids.append(candidate["document_id"])
            existing = existing_rows.get(candidate["document_id"])
            needs_rebuild = self._needs_rebuild(candidate, existing)
            row_changed = needs_rebuild or self._row_changed(candidate, existing)

            if needs_rebuild:
                if not candidate["text_content"].strip():
                    vector = self.vector_retriever.embed_text_for_signature(
                        candidate["text_content"],
                        active_signature,
                    )
                elif active_signature.get("embedding_backend") == "remote":
                    vector, actual_signature = self.vector_retriever.embed_text_with_signature(candidate["text_content"])
                    if not self._same_signature(active_signature, actual_signature):
                        return {"restart_signature": actual_signature}
                else:
                    vector = self.vector_retriever.embed_text_for_signature(
                        candidate["text_content"],
                        active_signature,
                    )
                rebuilt_count += 1
            else:
                vector = list(existing.get("vector", []) if existing else [])
                reused_count += 1

            stored_row = {
                **candidate,
                "vector": vector,
                "created_at": existing.get("created_at", now) if existing else now,
                "updated_at": now,
            }
            prepared_documents.append(
                {
                    "document_id": candidate["document_id"],
                    "source_type": candidate["source_type"],
                    "source_id": candidate["source_id"],
                    "summary": candidate["summary"],
                    "text": candidate["text_content"],
                    "metadata": candidate["metadata"],
                    "vector": vector,
                    "embedding_provider": candidate["embedding_provider"],
                    "embedding_backend": candidate["embedding_backend"],
                    "embedding_model": candidate["embedding_model"],
                    "embedding_dimensions": candidate["embedding_dimensions"],
                }
            )
            if row_changed:
                rows_to_upsert.append(stored_row)

        deleted_count = self.repository.delete_missing(document_ids)
        upserted_count = self.repository.upsert_documents(rows_to_upsert)
        return {
            "documents": prepared_documents,
            "persisted_document_count": len(prepared_documents),
            "reused_document_count": reused_count,
            "rebuilt_document_count": rebuilt_count,
            "deleted_document_count": deleted_count,
            "upserted_document_count": upserted_count,
            "vector_sync_last_updated_at": now.isoformat(),
            "embedding_signature": active_signature,
        }

    def _build_candidate(self, document: dict, signature: dict, now: datetime) -> dict:
        document_id = self._document_id(document["source_type"], document["source_id"])
        metadata = document.get("metadata", {})
        return {
            "document_id": document_id,
            "source_type": document["source_type"],
            "source_id": document["source_id"],
            "summary": document.get("summary"),
            "text_content": document.get("text", ""),
            "metadata": metadata,
            "content_hash": self._content_hash(
                text_content=document.get("text", ""),
                metadata=metadata,
                signature=signature,
            ),
            "embedding_provider": signature["embedding_provider"],
            "embedding_backend": signature["embedding_backend"],
            "embedding_model": signature["embedding_model"],
            "embedding_dimensions": signature["embedding_dimensions"],
            "created_at": now,
            "updated_at": now,
        }

    def _needs_rebuild(self, candidate: dict, existing: dict | None) -> bool:
        if existing is None:
            return True
        if existing.get("content_hash") != candidate["content_hash"]:
            return True
        if not self._same_signature(candidate, existing):
            return True
        vector = existing.get("vector", [])
        if not isinstance(vector, list):
            return True
        if len(vector) != int(candidate["embedding_dimensions"]):
            return True
        return False

    def _row_changed(self, candidate: dict, existing: dict | None) -> bool:
        if existing is None:
            return True
        return (
            existing.get("summary") != candidate.get("summary")
            or existing.get("text_content", "") != candidate.get("text_content", "")
            or existing.get("metadata", {}) != candidate.get("metadata", {})
            or existing.get("content_hash") != candidate.get("content_hash")
            or not self._same_signature(candidate, existing)
        )

    def _content_hash(self, text_content: str, metadata: dict, signature: dict) -> str:
        payload = {
            "text_content": text_content,
            "metadata": metadata,
            "embedding_provider": signature["embedding_provider"],
            "embedding_backend": signature["embedding_backend"],
            "embedding_model": signature["embedding_model"],
            "embedding_dimensions": signature["embedding_dimensions"],
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _document_id(self, source_type: str, source_id: str) -> str:
        return hashlib.sha1(f"{source_type}:{source_id}".encode("utf-8")).hexdigest()

    def _same_signature(self, left: dict, right: dict | None) -> bool:
        if right is None:
            return False
        return (
            left.get("embedding_provider") == right.get("embedding_provider")
            and left.get("embedding_backend") == right.get("embedding_backend")
            and left.get("embedding_model") == right.get("embedding_model")
            and int(left.get("embedding_dimensions", 0)) == int(right.get("embedding_dimensions", 0))
        )
