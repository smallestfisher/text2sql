from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest

from backend.app.models.retrieval import RetrievalHit
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.semantic_runtime import SemanticRuntime
from tests.fake_vector_retriever import FIXTURE_PATH, FakeVectorRetriever


REPO_ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_CASES_PATH = REPO_ROOT / "eval" / "retrieval_cases.json"


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FusionScoreTests(unittest.TestCase):
    def test_fusion_scores_are_normalized_inside_source_type_buckets(self) -> None:
        service = RetrievalService.__new__(RetrievalService)
        hits = [
            RetrievalHit(
                source_type="example",
                source_id="example_top",
                score=0.9,
                summary="",
                retrieval_channel="vector",
                source_score=0.9,
            ),
            RetrievalHit(
                source_type="example",
                source_id="example_second",
                score=0.8,
                summary="",
                retrieval_channel="vector",
                source_score=0.8,
            ),
            RetrievalHit(
                source_type="join_pattern",
                source_id="join_only_vector_hit",
                score=0.7,
                summary="",
                retrieval_channel="vector",
                source_score=0.7,
            ),
        ]

        service._apply_fusion_scores(hits)

        scores = {hit.source_id: hit.fusion_score for hit in hits}
        self.assertEqual(scores["example_top"], 1.0)
        self.assertEqual(scores["example_second"], 0.0)
        self.assertEqual(scores["join_only_vector_hit"], 1.0)


class RetrievalQuotaTests(unittest.TestCase):
    def test_retrieve_text_preserves_hits_up_to_source_type_quota_total(self) -> None:
        service = RetrievalService.__new__(RetrievalService)
        service.vector_retriever = type(
            "DisabledVectorRetriever",
            (),
            {"enabled": False},
        )()
        service.corpus_documents = []
        service._ensure_vector_ready = lambda: None
        service._unique = lambda values: list(dict.fromkeys(values))
        service._tokenize = lambda text: {"query"}
        service._retrieve_text_vector_hits = lambda query_text: []
        service._count_hits_by_source = RetrievalService._count_hits_by_source.__get__(service)
        service._count_hits_by_channel = RetrievalService._count_hits_by_channel.__get__(service)
        service._retrieval_channels = RetrievalService._retrieval_channels.__get__(service)
        service._domains_from_hits = lambda hits: []
        service._metrics_from_hits = lambda hits: []
        service._apply_fusion_scores = lambda hits: [
            setattr(hit, "fusion_score", hit.score) for hit in hits
        ]
        service._hit_dedup_key = RetrievalService._hit_dedup_key.__get__(service)
        service._source_priority = RetrievalService._source_priority.__get__(service)
        service._rerank_hits = RetrievalService._rerank_hits.__get__(service)
        service._select_top_hits = RetrievalService._select_top_hits.__get__(service)
        service._retrieve_text_document_hits = lambda query_tokens: [
            RetrievalHit(source_type="example", source_id="example_1", score=9, summary=""),
            RetrievalHit(source_type="example", source_id="example_2", score=8, summary=""),
            RetrievalHit(source_type="table_schema", source_id="table_1", score=7, summary=""),
            RetrievalHit(source_type="table_schema", source_id="table_2", score=6, summary=""),
            RetrievalHit(source_type="knowledge", source_id="knowledge_1", score=5, summary=""),
            RetrievalHit(source_type="knowledge", source_id="knowledge_2", score=4, summary=""),
            RetrievalHit(source_type="join_pattern", source_id="join_1", score=3, summary=""),
        ]

        retrieval = RetrievalService.retrieve_text(
            service,
            question="query",
            semantic_brief="query",
        )

        self.assertEqual(
            [hit.source_id for hit in retrieval.hits],
            [
                "example_1",
                "example_2",
                "table_1",
                "table_2",
                "knowledge_1",
                "knowledge_2",
                "join_1",
            ],
        )


@unittest.skipUnless(
    os.environ.get("RUN_VECTOR_FUSION_TESTS") == "1" and FIXTURE_PATH.exists(),
    "vector fusion test is opt-in; set RUN_VECTOR_FUSION_TESTS=1 (needs the embedding fixture)",
)
class VectorFusionTests(unittest.TestCase):
    """Exercise the keyword+vector fusion path offline via a fixture.

    The default retrieval eval test runs with vector disabled, so it cannot
    cover evidence that only surfaces once the vector channel participates in
    ranking. This test wires a RetrievalService with FakeVectorRetriever (real
    fusion logic, embeddings served from a precomputed fixture) and asserts that
    the vector-only expectations are met.
    """

    def setUp(self) -> None:
        # Build per-test (not setUpClass): the loaded vector index is process
        # state that other test classes constructing a RetrievalService can
        # reset, so each test rebuilds and reloads its own fixture-backed index.
        domain_config = DomainConfigLoader().load()
        self.semantic_runtime = SemanticRuntime(domain_config)
        self.prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)

        vector_retriever = FakeVectorRetriever()
        service = RetrievalService(
            domain_config=domain_config,
            semantic_runtime=self.semantic_runtime,
            vector_retriever=vector_retriever,
        )

        # Attach fixture vectors to the corpus and load them directly, bypassing
        # the DB-backed corpus store. This mirrors what _sync_vector_index would
        # hand to load_documents in production.
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["embeddings"]
        documents_with_vectors = []
        for document in service.corpus_documents:
            vector = fixture.get(_text_key(document["text"]))
            if vector is None:
                continue
            documents_with_vectors.append({**document, "vector": vector})
        vector_retriever.load_documents(documents_with_vectors)

        # Clear the pending-rebuild gate so retrieve_text treats the index as
        # ready (we loaded it directly instead of through the store).
        service.last_vector_sync_summary = {
            **service.last_vector_sync_summary,
            "pending_rebuild": False,
            "error": None,
            "embedding_signature": vector_retriever.embedding_signature(),
        }
        self.service = service
        self.loaded_vector_count = len(documents_with_vectors)

    def test_vector_channel_is_actually_active(self) -> None:
        self.assertTrue(self.service.vector_retriever.enabled)
        self.assertTrue(self.service.vector_retriever.ready)
        self.assertGreater(self.loaded_vector_count, 0)

    def test_vector_only_expectations_are_recovered(self) -> None:
        cases = json.loads(RETRIEVAL_CASES_PATH.read_text(encoding="utf-8"))
        failures: list[str] = []
        saw_vector_only_case = False

        for case in cases:
            vector_only = case.get("vector_only_expected_join_pattern_ids", [])
            if not vector_only:
                continue
            saw_vector_only_case = True
            question = str(case["question"])
            retrieval = self.service.retrieve_text(
                question=question,
                semantic_brief=question,
            )
            # The vector channel must be active for this retrieval.
            self.assertIn(
                "vector",
                retrieval.retrieval_channels,
                msg="vector channel should be active",
            )
            sql_context = SqlGenerationContext(
                question_type="new",
                subject_domain=case["subject_domain"],
                tables=[],
                semantic_brief=question,
            )
            prompt = self.prompt_builder.build_sql_prompt(
                sql_context,
                retrieval=retrieval,
                question=question,
            )
            join_pattern_ids = set(prompt["context_summary"]["join_pattern_ids"])
            missing = [item for item in vector_only if item not in join_pattern_ids]
            if missing:
                failures.append(
                    f"{case['id']} missing vector-only join patterns {missing}; "
                    f"actual={sorted(join_pattern_ids)}"
                )

        self.assertTrue(saw_vector_only_case, "no vector-only case to validate")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
