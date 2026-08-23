from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.app.repositories.file_vector_document_repository import FileVectorDocumentRepository
from backend.app.services.vector_corpus_store_service import VectorCorpusStoreService


class CountingVectorRetriever:
    enabled = True

    def __init__(self) -> None:
        self.calls = 0
        self.signature = {
            "embedding_provider": "openai",
            "embedding_backend": "remote",
            "embedding_model": "test-embedding",
            "embedding_dimensions": 4,
        }

    def embedding_signature(self) -> dict:
        return dict(self.signature)

    def embed_text_with_signature(self, text: str) -> tuple[list[float], dict]:
        self.calls += 1
        value = float(self.calls)
        return [value, value + 1, value + 2, value + 3], dict(self.signature)


def _documents(changed_index: int | None = None) -> list[dict]:
    documents = []
    for index in range(11):
        text = f"daily_plan schema document {index}"
        if index == changed_index:
            text += " with a new Chinese description"
        documents.append(
            {
                "source_type": "table_schema",
                "source_id": f"daily_plan:{index}",
                "summary": f"daily_plan {index}",
                "text": text,
                "metadata": {"table": "daily_plan", "index": index},
            }
        )
    return documents


class VectorCorpusStoreTests(unittest.TestCase):
    def test_new_release_reuses_vectors_by_content_hash(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            repository = FileVectorDocumentRepository(Path(temporary_directory))
            retriever = CountingVectorRetriever()

            first = VectorCorpusStoreService(repository, retriever, "release_v1").sync(_documents())
            second = VectorCorpusStoreService(repository, retriever, "release_v2").sync(
                _documents(changed_index=3)
            )

            self.assertEqual(first.rebuilt_document_count, 11)
            self.assertEqual(first.reused_document_count, 0)
            self.assertEqual(second.rebuilt_document_count, 1)
            self.assertEqual(second.reused_document_count, 10)
            self.assertEqual(retriever.calls, 12)
            self.assertEqual(second.upserted_document_count, 11)


if __name__ == "__main__":
    unittest.main()
