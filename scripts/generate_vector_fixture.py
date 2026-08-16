"""Generate an offline embedding fixture for vector-enabled retrieval tests.

The unit tests run with the vector channel disabled, so they cannot verify the
score-fusion behaviour that only matters once vector ranking participates. This
script calls the real embedding API once and stores the resulting vectors keyed
by a sha256 of the embedded text. Tests then load these vectors through a fake
retriever (no network), exercising the real fusion/search code path offline.

Regenerate when the corpus (semantic assets / examples / retrieval cases) or the
embedding model changes:

    .venv/bin/python scripts/generate_vector_fixture.py

It loads .env (needs VECTOR_API_KEY), embeds every corpus document text plus
each retrieval-case question, and writes tests/fixtures/vector_embeddings.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "vector_embeddings.json"


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    _load_dotenv()

    from backend.app.services.retrieval_service import RetrievalService
    from backend.app.services.vector_retriever import VectorRetriever
    from tests.fixture_metadata import fixture_semantic_runtime

    api_key = os.environ.get("VECTOR_API_KEY")
    api_base = os.environ.get("VECTOR_API_BASE")
    model_name = (
        os.environ.get("VECTOR_EMBEDDING_MODEL")
        or os.environ.get("VECTOR_EMBEDING_MODEL")
        or "BAAI/bge-m3"
    )
    dimensions = int(os.environ.get("VECTOR_DIMENSIONS") or 1024)
    if not api_key:
        raise SystemExit("VECTOR_API_KEY is required to generate the fixture")

    retriever = VectorRetriever(
        provider="openai",
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        dimensions=dimensions,
    )

    domain_config, metadata_registry, semantic_runtime = fixture_semantic_runtime()
    service = RetrievalService(
        domain_config=domain_config,
        semantic_runtime=semantic_runtime,
        metadata_registry=metadata_registry,
    )

    texts: set[str] = {doc["text"] for doc in service.corpus_documents}
    cases = json.loads(
        (REPO_ROOT / "eval" / "retrieval_cases.json").read_text(encoding="utf-8")
    )
    for case in cases:
        texts.add(str(case["question"]))

    print(f"embedding {len(texts)} unique texts with model={model_name} dim={dimensions}")
    embeddings: dict[str, list[float]] = {}
    for index, text in enumerate(sorted(texts), start=1):
        embeddings[_text_key(text)] = retriever.embed_text(text)
        if index % 20 == 0:
            print(f"  embedded {index}/{len(texts)}")

    payload = {
        "model": model_name,
        "dimensions": dimensions,
        "embeddings": embeddings,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    size_kb = FIXTURE_PATH.stat().st_size / 1024
    print(f"wrote {FIXTURE_PATH} ({len(embeddings)} vectors, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
