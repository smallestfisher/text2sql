from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.app.services.vector_retriever import VectorRetriever


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "vector_embeddings.json"


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeVectorRetriever(VectorRetriever):
    """Offline VectorRetriever backed by a pre-computed embedding fixture.

    It reports ``enabled=True`` and runs the real ``load_documents``/``search``/
    ``_cosine_similarity`` logic, but resolves embeddings from a fixture keyed by
    the sha256 of the embedded text instead of calling the embedding API. This
    lets tests exercise the vector-enabled ranking path (and the keyword/vector
    fusion) without network access. Texts missing from the fixture raise, so a
    stale fixture fails loudly rather than silently degrading recall.
    """

    def __init__(self, fixture_path: Path | None = None) -> None:
        payload = json.loads(
            (fixture_path or FIXTURE_PATH).read_text(encoding="utf-8")
        )
        dimensions = int(payload.get("dimensions") or 1024)
        super().__init__(
            provider="openai",
            api_key="fixture-key",
            api_base="https://fixture.invalid/v1",
            model_name=str(payload.get("model") or "BAAI/bge-m3"),
            dimensions=dimensions,
        )
        # Force enabled without a real OpenAI client; _remote_embed is overridden
        # so the sentinel is never used for network calls.
        self.client = object()
        self._embeddings: dict[str, list[float]] = {
            key: [float(value) for value in vector]
            for key, vector in payload.get("embeddings", {}).items()
        }

    def _remote_embed(self, text: str) -> list[float]:
        vector = self._embeddings.get(_text_key(text))
        if vector is None:
            raise RuntimeError(
                "vector fixture has no embedding for the requested text; "
                "regenerate tests/fixtures/vector_embeddings.json via "
                "scripts/generate_vector_fixture.py"
            )
        return self._normalize(vector)

    def embed_text_for_signature(self, text: str, signature: dict) -> list[float]:
        # The real implementation rejects any backend other than "remote".
        # Documents loaded directly in tests may carry no signature, so bypass
        # that check and resolve straight from the fixture.
        _ = signature
        if not text.strip():
            return [0.0] * self.dimensions
        return self._remote_embed(text)
