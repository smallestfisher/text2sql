from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
import threading

from openai import OpenAI


@dataclass
class VectorDocument:
    source_type: str
    source_id: str
    summary: str
    text: str
    metadata: dict
    vector: list[float]


class VectorRetriever:
    def __init__(
        self,
        provider: str = "local",
        api_key: str | None = None,
        api_base: str | None = None,
        model_name: str = "text-embedding-3-small",
        dimensions: int = 256,
        timeout_seconds: int = 20,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.dimensions = max(32, dimensions)
        self.timeout_seconds = timeout_seconds
        self.documents: list[VectorDocument] = []
        self._documents_lock = threading.RLock()
        self.client = None
        self._last_search_error: str | None = None
        self._indexed_document_count = 0
        self._loaded_embedding_signature: dict | None = None
        if self.provider in {"openai", "compatible", "siliconflow"} and self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        self._ready = not self.enabled

    @property
    def enabled(self) -> bool:
        return self.provider != "disabled" and self.client is not None

    @property
    def ready(self) -> bool:
        with self._documents_lock:
            return self._ready

    def health(self) -> dict:
        with self._documents_lock:
            ready = self._ready
            indexed_document_count = self._indexed_document_count
            last_search_error = self._last_search_error
            loaded_embedding_signature = self._loaded_embedding_signature
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model_name if self.client is not None else None,
            "api_base": self.api_base if self.client is not None else None,
            "ready": ready,
            "indexed_document_count": indexed_document_count,
            "last_search_error": last_search_error,
            "configured_embedding_signature": self.embedding_signature(),
            "loaded_embedding_signature": loaded_embedding_signature,
        }

    def embedding_signature(self) -> dict | None:
        if not self.enabled:
            return None
        return {
            "embedding_provider": self.provider,
            "embedding_backend": "remote",
            "embedding_model": self.model_name,
            "embedding_dimensions": self.dimensions,
        }

    def embed_text(self, text: str) -> list[float]:
        vector, _ = self.embed_text_with_signature(text)
        return vector

    def embed_text_with_signature(self, text: str) -> tuple[list[float], dict]:
        if not self.enabled:
            raise RuntimeError("vector embedding client is not configured")
        signature = self.embedding_signature() or {}
        if not text.strip():
            return [0.0] * self.dimensions, signature
        return self._remote_embed(text), signature

    def load_documents(self, documents: list[dict]) -> None:
        if not self.enabled:
            with self._documents_lock:
                self.documents = []
                self._ready = True
                self._last_search_error = None
                self._indexed_document_count = 0
                self._loaded_embedding_signature = None
            return

        indexed_documents = self._build_vector_documents(documents)
        loaded_embedding_signature = self._extract_embedding_signature(documents)
        with self._documents_lock:
            self.documents = indexed_documents
            self._ready = True
            self._last_search_error = None
            self._indexed_document_count = len(indexed_documents)
            self._loaded_embedding_signature = loaded_embedding_signature

    def search(
        self,
        query_text: str,
        top_k: int = 5,
        source_types: list[str] | None = None,
    ) -> list[dict]:
        if not self.enabled or not query_text.strip() or top_k <= 0:
            return []

        allowed_source_types = set(source_types or [])
        scored: list[dict] = []
        with self._documents_lock:
            documents = list(self.documents)
            loaded_embedding_signature = self._loaded_embedding_signature

        try:
            query_vector, query_signature = self.embed_text_with_signature(query_text)
        except Exception as exc:
            with self._documents_lock:
                self._last_search_error = str(exc)
            raise RuntimeError(f"vector query embedding failed: {exc}") from exc
        if documents and loaded_embedding_signature and not self._same_signature(loaded_embedding_signature, query_signature):
            with self._documents_lock:
                self._last_search_error = (
                    "query embedding signature does not match loaded corpus signature"
                )
            raise RuntimeError("query embedding signature does not match loaded corpus signature")
        with self._documents_lock:
            self._last_search_error = None

        for document in documents:
            if allowed_source_types and document.source_type not in allowed_source_types:
                continue
            score = self._cosine_similarity(query_vector, document.vector)
            if score <= 0:
                continue
            scored.append(
                {
                    "source_type": document.source_type,
                    "source_id": document.source_id,
                    "summary": document.summary,
                    "score": round(score, 6),
                    "metadata": document.metadata,
                }
            )

        if len(scored) <= top_k:
            scored.sort(key=lambda item: item["score"], reverse=True)
            return scored
        ranked = heapq.nlargest(
            top_k,
            enumerate(scored),
            key=lambda item: (item[1]["score"], -item[0]),
        )
        return [item for _, item in ranked]

    def _build_vector_documents(self, documents: list[dict]) -> list[VectorDocument]:
        return [
            VectorDocument(
                source_type=item["source_type"],
                source_id=item["source_id"],
                summary=item.get("summary", item["source_id"]),
                text=item.get("text", ""),
                metadata=item.get("metadata", {}),
                vector=self._normalize([float(value) for value in item.get("vector", [])]),
            )
            for item in documents
        ]

    def _remote_embed(self, text: str) -> list[float]:
        if self.client is None:
            raise RuntimeError("vector embedding client is not configured")
        request_payload = {
            "model": self.model_name,
            "input": text,
            "timeout": self.timeout_seconds,
        }
        if self.model_name.startswith("Qwen/Qwen3-Embedding-"):
            request_payload["dimensions"] = self.dimensions
        response = self.client.embeddings.create(**request_payload)
        vector = list(response.data[0].embedding)
        if not vector:
            raise RuntimeError("vector embedding response is empty")
        return self._normalize(vector)

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        return max(0.0, sum(a * b for a, b in zip(left, right)))

    def _normalize(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(item * item for item in vector))
        if norm == 0:
            return vector
        return [item / norm for item in vector]

    def _extract_embedding_signature(self, documents: list[dict]) -> dict | None:
        if not documents:
            return None
        signatures = set()
        for item in documents:
            signature = (
                item.get("embedding_provider"),
                item.get("embedding_backend"),
                item.get("embedding_model"),
                int(item.get("embedding_dimensions", 0)),
            )
            if not all(signature):
                raise ValueError("loaded vector documents are missing embedding signatures")
            signatures.add(signature)
        if len(signatures) > 1:
            raise ValueError("loaded vector documents have mixed embedding signatures")
        provider, backend, model, dimensions = next(iter(signatures))
        return {
            "embedding_provider": provider,
            "embedding_backend": backend,
            "embedding_model": model,
            "embedding_dimensions": dimensions,
        }

    def _same_signature(self, left: dict, right: dict | None) -> bool:
        if right is None:
            return False
        return (
            left.get("embedding_provider") == right.get("embedding_provider")
            and left.get("embedding_backend") == right.get("embedding_backend")
            and left.get("embedding_model") == right.get("embedding_model")
            and int(left.get("embedding_dimensions", 0)) == int(right.get("embedding_dimensions", 0))
        )
