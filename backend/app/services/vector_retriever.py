from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re

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
        self.client = None
        if self.provider in {"openai", "compatible", "siliconflow"} and self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)

    @property
    def enabled(self) -> bool:
        return self.provider != "disabled"

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model_name if self.client is not None else ("local-hash" if self.enabled else None),
            "api_base": self.api_base if self.client is not None else None,
        }

    def index_documents(self, documents: list[dict]) -> None:
        if not self.enabled:
            self.documents = []
            return

        self.documents = [
            VectorDocument(
                source_type=item["source_type"],
                source_id=item["source_id"],
                summary=item.get("summary", item["source_id"]),
                text=item.get("text", ""),
                metadata=item.get("metadata", {}),
                vector=self._embed(item.get("text", "")),
            )
            for item in documents
        ]

    def search(
        self,
        query_text: str,
        top_k: int = 5,
        source_types: list[str] | None = None,
    ) -> list[dict]:
        if not self.enabled or not query_text.strip():
            return []

        query_vector = self._embed(query_text)
        allowed_source_types = set(source_types or [])
        scored: list[dict] = []

        for document in self.documents:
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

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def _embed(self, text: str) -> list[float]:
        if self.client is not None and text.strip():
            try:
                return self._remote_embed(text)
            except Exception:
                pass

        return self._local_embed(text)

    def _remote_embed(self, text: str) -> list[float]:
        response = self.client.embeddings.create(
            model=self.model_name,
            input=text,
            timeout=self.timeout_seconds,
        )
        vector = list(response.data[0].embedding)
        if not vector:
            return self._local_embed(text)
        return self._normalize(vector)

    def _local_embed(self, text: str) -> list[float]:
        tokens = self._tokenize(text)
        vector = [0.0] * self.dimensions
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.dimensions
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            weight = 1.0 + min(len(token), 12) / 12.0
            vector[index] += sign * weight

        norm = math.sqrt(sum(item * item for item in vector))
        if norm == 0:
            return vector
        return [item / norm for item in vector]

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        return max(0.0, sum(a * b for a, b in zip(left, right)))

    def _tokenize(self, text: str) -> list[str]:
        ascii_tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", text)
            if len(token) > 1
        ]
        chinese_chunks = [
            chunk
            for chunk in re.findall(r"[\u4e00-\u9fa5]{2,}", text)
            if len(chunk) >= 2
        ]
        return ascii_tokens + chinese_chunks

    def _normalize(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(item * item for item in vector))
        if norm == 0:
            return vector
        return [item / norm for item in vector]
