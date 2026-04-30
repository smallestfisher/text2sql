from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.api import dependencies
from backend.app.api.routes.chat import chat_query_stream
from backend.app.models.api import PlanRequest
from backend.app.services.progress_service import ProgressService
from backend.app.services.vector_retriever import VectorRetriever
from backend.app.utils import atomic_write_text


class ContainerResetTests(unittest.TestCase):
    def tearDown(self) -> None:
        dependencies.get_container.cache_clear()

    def test_reset_container_rebuilds_cached_singleton(self) -> None:
        first = SimpleNamespace(name="first")
        second = SimpleNamespace(name="second")
        with patch("backend.app.api.dependencies.AppContainer", side_effect=[first, second]) as factory:
            cached = dependencies.get_container()
            cached_again = dependencies.get_container()
            rebuilt = dependencies.reset_container()

        self.assertIs(cached, cached_again)
        self.assertIs(cached, first)
        self.assertIs(rebuilt, second)
        self.assertEqual(factory.call_count, 2)


class VectorRetrieverTests(unittest.TestCase):
    def test_remote_client_is_required_when_local_hash_is_removed(self) -> None:
        retriever = VectorRetriever(provider="siliconflow", api_key=None, dimensions=128)

        self.assertFalse(retriever.enabled)
        self.assertIsNone(retriever.embedding_signature())
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            retriever.embed_text_with_signature("查询库存")


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_text_replaces_existing_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "sample.txt"
            atomic_write_text(target, "first\n")
            atomic_write_text(target, "second\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")


class StreamingRouteTests(unittest.TestCase):
    def test_stream_emits_failed_event_when_background_task_raises(self) -> None:
        class FakeAuditService:
            def new_trace(self):
                return SimpleNamespace(trace_id="trace_test")

        class FakeOrchestrator:
            def __init__(self, progress_service: ProgressService) -> None:
                self.progress_service = progress_service

            def chat(self, request: PlanRequest, trace_id: str):
                self.progress_service.complete(trace_id)
                raise RuntimeError("boom")

        class FakeContainer:
            def __init__(self) -> None:
                self.progress_service = ProgressService()
                self.audit_service = FakeAuditService()
                self.orchestrator = FakeOrchestrator(self.progress_service)

        async def run_case() -> str:
            response = await chat_query_stream(
                request=PlanRequest(question="test question"),
                http_request=SimpleNamespace(headers={}),
                container=FakeContainer(),
            )
            chunks: list[bytes] = []
            iterator = response.body_iterator.__aiter__()
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=1)
                    except StopAsyncIteration:
                        break
                    chunks.append(chunk)
            finally:
                close_stream = getattr(response.body_iterator, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            return b"".join(chunks).decode("utf-8")

        with patch("backend.app.api.routes.chat.logger.exception"):
            payload = asyncio.run(run_case())
        self.assertIn("event: failed", payload)
        self.assertIn("boom", payload)


if __name__ == "__main__":
    unittest.main()
