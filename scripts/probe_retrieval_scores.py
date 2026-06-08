"""Read-only probe: dump keyword (BM25) vs vector (cosine) score distributions.

Purpose: before changing the score-fusion logic in RetrievalService, we need to
see the real magnitude of both channels. The unit tests run with vector
disabled, so they hide the fusion problem. This script loads .env (real
VECTOR_API_KEY), builds the same corpus documents RetrievalService builds,
embeds the query and corpus docs directly via VectorRetriever, and prints the
raw score ranges per channel for each retrieval eval case.

It does NOT touch the database and does NOT modify any code. It only calls the
embedding API (small number of requests).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def main() -> None:
    _load_dotenv()

    from backend.app.services.domain_config_loader import DomainConfigLoader
    from backend.app.services.retrieval_service import RetrievalService
    from backend.app.services.semantic_runtime import SemanticRuntime
    from backend.app.services.vector_retriever import VectorRetriever

    api_key = os.environ.get("VECTOR_API_KEY")
    api_base = os.environ.get("VECTOR_API_BASE")
    model_name = (
        os.environ.get("VECTOR_EMBEDDING_MODEL")
        or os.environ.get("VECTOR_EMBEDING_MODEL")
        or "BAAI/bge-m3"
    )
    print(f"vector model={model_name} api_base={api_base} key_set={bool(api_key)}")

    domain_config = DomainConfigLoader().load()
    semantic_runtime = SemanticRuntime(domain_config)

    retriever = VectorRetriever(
        provider="openai",
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
    )
    print(f"vector enabled={retriever.enabled}")

    service = RetrievalService(
        domain_config=domain_config,
        semantic_runtime=semantic_runtime,
    )
    documents = service.corpus_documents
    print(f"corpus documents={len(documents)}")

    cases = json.loads(
        (REPO_ROOT / "eval" / "retrieval_cases.json").read_text(encoding="utf-8")
    )

    # Embed the whole corpus once (cosine needs doc vectors).
    print("embedding corpus (one API call per doc)...")
    doc_vectors: dict[str, list[float]] = {}
    for doc in documents:
        key = f"{doc['source_type']}::{doc['source_id']}"
        try:
            doc_vectors[key] = retriever.embed_text(doc["text"])
        except Exception as exc:  # noqa: BLE001 - probe should not crash hard
            print(f"  embed failed for {key}: {exc}")

    def cosine(a: list[float], b: list[float]) -> float:
        num = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return num / (na * nb)

    for case in cases:
        question = str(case["question"])
        print("\n" + "=" * 70)
        print(f"CASE {case['id']}")
        print(f"Q: {question}")

        # Keyword channel via the service's own tokenizer + BM25.
        query_tokens = sorted(service._tokenize(question))
        kw_hits = service._retrieve_text_document_hits(query_tokens)
        kw_hits.sort(key=lambda h: h.score, reverse=True)
        if kw_hits:
            kw_scores = [h.score for h in kw_hits]
            print(
                f"  KEYWORD: n={len(kw_hits)} max={max(kw_scores):.3f} "
                f"min={min(kw_scores):.3f}"
            )
            for h in kw_hits[:5]:
                print(f"    kw {h.score:7.3f}  {h.source_type:13} {h.source_id}")

        # Vector channel: cosine of query against every corpus doc.
        try:
            q_vec = retriever.embed_text(question)
        except Exception as exc:  # noqa: BLE001
            print(f"  VECTOR: query embed failed: {exc}")
            continue
        vec_scored = []
        for doc in documents:
            key = f"{doc['source_type']}::{doc['source_id']}"
            vec = doc_vectors.get(key)
            if vec is None:
                continue
            vec_scored.append((cosine(q_vec, vec), doc))
        vec_scored.sort(key=lambda item: item[0], reverse=True)
        if vec_scored:
            vscores = [s for s, _ in vec_scored]
            print(
                f"  VECTOR : n={len(vec_scored)} max={max(vscores):.3f} "
                f"min={min(vscores):.3f}"
            )
            for score, doc in vec_scored[:5]:
                print(
                    f"    ve {score:7.3f}  {doc['source_type']:13} {doc['source_id']}"
                )


if __name__ == "__main__":
    main()
