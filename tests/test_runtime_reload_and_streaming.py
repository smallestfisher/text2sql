from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from backend.app.api import dependencies
from backend.app.api.routes.chat import chat_query_stream
from backend.app.core.exceptions import ClientCancelledError
from backend.app.models.admin import RuntimeQueryLogRecord, RuntimeSqlAuditRecord
from backend.app.models.api import ChatResponse, PlanRequest, ValidationResponse
from backend.app.models.classification import QuestionClassification
from backend.app.models.conversation import ChatMessage, ChatSession

from backend.app.models.context_summary import ContextSummary
from backend.app.models.session_state import SessionState
from backend.app.models.trace import TraceRecord
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.progress_service import ProgressService
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.services.session_workspace_service import SessionWorkspaceService
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.llm_client import LLMClient, sqlglot as llm_sqlglot
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


class RetrievalServiceFailFastTests(unittest.TestCase):
    def test_retrieval_corpus_excludes_domain_config_metrics(self) -> None:
        domain_config = DomainConfigLoader().load()
        service = RetrievalService(domain_config=domain_config)

        source_types = {document["source_type"] for document in service.corpus_documents}

        self.assertIn("example", source_types)
        self.assertIn("knowledge", source_types)
        self.assertIn("table_schema", source_types)
        self.assertIn("join_pattern", source_types)
        self.assertNotIn("metric", source_types)
        self.assertFalse(
            any(
                document["source_type"] == "knowledge"
                and document["source_id"].startswith("table:")
                for document in service.corpus_documents
            )
        )
        table_schema_documents = [
            document
            for document in service.corpus_documents
            if document["source_type"] == "table_schema"
        ]
        self.assertTrue(table_schema_documents)
        self.assertTrue(
            all(document["metadata"].get("table") for document in table_schema_documents)
        )

    def test_example_vector_documents_include_sql_structure(self) -> None:
        domain_config = DomainConfigLoader().load()
        service = RetrievalService(domain_config=domain_config)

        document = next(
            item
            for item in service.corpus_documents
            if item["source_type"] == "example"
            and item["source_id"] == "demand_latest5_p_202604_top_fgcode_001"
        )

        text = document["text"]
        sql_features = document["metadata"]["sql_features"]

        self.assertIn("sql_outline:", text)
        self.assertIn("union_all_unpivot", text)
        self.assertIn("regexp_version_parse", text)
        self.assertIn("REQUIREMENT_QTY", text)
        self.assertIn("MONTH7", text)
        self.assertIn("PM_VERSION", sql_features["referenced_fields"])
        self.assertIn("union_all_unpivot", sql_features["sql_patterns"])

    def test_retrieval_service_raises_when_vector_client_is_missing(self) -> None:
        domain_config = DomainConfigLoader().load()
        retriever = VectorRetriever(provider="siliconflow", api_key=None, dimensions=128)

        with self.assertRaisesRegex(RuntimeError, "vector embedding client is not configured"):
            RetrievalService(
                domain_config=domain_config,
                vector_retriever=retriever,
            )

    def test_retrieval_service_raises_when_vector_prewarm_fails(self) -> None:
        class FakeVectorRetriever:
            provider = "siliconflow"
            enabled = True
            ready = False

            def embedding_signature(self):
                return {"embedding_provider": "siliconflow"}

            def health(self):
                return {"ready": False, "indexing": False}

            def load_documents(self, documents):
                return None

        class FakeVectorCorpusStoreService:
            def sync(self, corpus_documents):
                raise RuntimeError("boom")

        domain_config = DomainConfigLoader().load()

        with self.assertRaisesRegex(RuntimeError, "vector corpus sync failed"):
            RetrievalService(
                domain_config=domain_config,
                vector_retriever=FakeVectorRetriever(),
                vector_corpus_store_service=FakeVectorCorpusStoreService(),
                prewarm_vector_index=True,
            )


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_text_replaces_existing_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "sample.txt"
            atomic_write_text(target, "first\n")
            atomic_write_text(target, "second\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")


class DatabaseConnectorFailFastTests(unittest.TestCase):
    def test_execute_readonly_raises_when_not_configured(self) -> None:
        connector = DatabaseConnector()

        with self.assertRaisesRegex(RuntimeError, "database connector is not configured"):
            connector.execute_readonly("SELECT 1")

    def test_execute_readonly_stops_when_session_timeout_cannot_be_applied(self) -> None:
        class FakeConnection:
            def exec_driver_sql(self, sql: str) -> None:
                raise SQLAlchemyError("permission denied")

            def execute(self, statement):
                raise AssertionError("sql should not execute when session timeout setup fails")

        class FakeConnectContext:
            def __enter__(self):
                return FakeConnection()

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        connector = DatabaseConnector(timeout_seconds=30)
        connector.engine = SimpleNamespace(connect=lambda: FakeConnectContext())

        execution = connector.execute_readonly("SELECT 1")

        self.assertFalse(execution.executed)
        self.assertEqual(execution.status, "db_error")
        self.assertEqual(execution.error_category, "configuration")
        self.assertIn("failed to apply session max execution time", execution.errors[0])


class RuntimeQueryLogHydrationTests(unittest.TestCase):
    def test_query_log_extracts_total_elapsed_ms_from_chat_total_step(self) -> None:
        connector = type(
            "FakeRuntimeConnector",
            (),
            {
                "fetch_all": lambda _self, sql, params=None: [],
                "execute_write": lambda _self, sql, params=None: 1,
            },
        )()
        repository = DbRuntimeLogRepository(connector)

        record = repository._hydrate_query_log(
            {
                "trace_id": "trace_elapsed",
                "session_id": "session_1",
                "user_id": "user_1",
                "question": "latest inventory",
                "question_type": "new",
                "subject_domain": "inventory",
                "answer_status": "ok",
                "context_valid": True,
                "context_risk_level": "low",
                "context_risk_flags_json": "[]",
                "sql_valid": True,
                "sql_risk_level": "low",
                "sql_risk_flags_json": "[]",
                "executed": True,
                "row_count": 1,
                "warnings_json": "[]",
                "trace_json": '{"steps":[{"name":"chat_total","status":"completed","metadata":{"elapsed_ms":1532}}]}',
                "created_at": datetime.utcnow(),
            }
        )

        self.assertEqual(record.total_elapsed_ms, 1532)


class StreamingRouteTests(unittest.TestCase):
    def test_stream_emits_failed_event_when_background_task_raises(self) -> None:
        class FakeAuditService:
            def new_trace(self):
                return SimpleNamespace(trace_id="trace_test")

        class FakeOrchestrator:
            def __init__(self, progress_service: ProgressService) -> None:
                self.progress_service = progress_service

            def chat(self, request: PlanRequest, trace_id: str, cancellation_token=None):
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
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=3)
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

    def test_stream_cancels_background_work_when_client_disconnects(self) -> None:
        class FakeRequest:
            def __init__(self) -> None:
                self.headers = {}
                self._poll_count = 0

            async def is_disconnected(self) -> bool:
                self._poll_count += 1
                return self._poll_count >= 2

        class FakeAuditService:
            def new_trace(self):
                return SimpleNamespace(trace_id="trace_cancel")

        class FakeOrchestrator:
            def __init__(self, progress_service: ProgressService) -> None:
                self.progress_service = progress_service
                self.cancelled = False

            def chat(self, request: PlanRequest, trace_id: str, cancellation_token=None):
                if cancellation_token is None or not cancellation_token._event.wait(timeout=2):
                    self.progress_service.complete(trace_id)
                    raise AssertionError("expected cancellation token to be triggered")
                self.cancelled = True
                self.progress_service.complete(trace_id)
                raise ClientCancelledError("client disconnected during question_analysis")

        class FakeContainer:
            def __init__(self) -> None:
                self.progress_service = ProgressService()
                self.audit_service = FakeAuditService()
                self.orchestrator = FakeOrchestrator(self.progress_service)

        async def run_case() -> tuple[str, bool]:
            container = FakeContainer()
            response = await chat_query_stream(
                request=PlanRequest(question="test question"),
                http_request=FakeRequest(),
                container=container,
            )
            chunks: list[bytes] = []
            iterator = response.body_iterator.__aiter__()
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=3)
                    except StopAsyncIteration:
                        break
                    chunks.append(chunk)
            finally:
                close_stream = getattr(response.body_iterator, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            return b"".join(chunks).decode("utf-8"), container.orchestrator.cancelled

        payload, cancelled = asyncio.run(run_case())
        self.assertTrue(cancelled)
        self.assertNotIn("event: failed", payload)


class LLMClientSqlExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        if llm_sqlglot is None:
            self.client = None
            return
        self.client = LLMClient()

    def test_extract_sql_from_think_block_and_fenced_sql(self) -> None:
        if llm_sqlglot is None:
            with self.assertRaisesRegex(RuntimeError, "sqlglot is required"):
                LLMClient()
            return
        content = """
<think>
先分析表和字段，再输出 SQL。
</think>
```sql
SELECT factory_code, SUM(qty) AS total_qty
FROM inventory
GROUP BY factory_code
LIMIT 20;
```
"""

        sql = self.client._extract_sql(content)

        self.assertEqual(
            sql,
            "SELECT factory_code, SUM(qty) AS total_qty\nFROM inventory\nGROUP BY factory_code\nLIMIT 20;",
        )

    def test_extract_sql_ignores_prefix_and_trailing_explanation(self) -> None:
        if llm_sqlglot is None:
            with self.assertRaisesRegex(RuntimeError, "sqlglot is required"):
                LLMClient()
            return
        content = """
下面是 SQL：
SELECT biz_month, SUM(input_qty) AS total_input
FROM production_actuals
GROUP BY biz_month
LIMIT 50
说明：按月份汇总实际投入。
"""

        sql = self.client._extract_sql(content)

        self.assertEqual(
            sql,
            "SELECT biz_month, SUM(input_qty) AS total_input\nFROM production_actuals\nGROUP BY biz_month\nLIMIT 50;",
        )


class MetadataRegistryFailFastTests(unittest.TestCase):
    def test_invalid_examples_template_json_raises(self) -> None:
        with TemporaryDirectory() as temp_dir:
            examples_path = Path(temp_dir) / "examples.json"
            tables_path = Path(temp_dir) / "tables.json"
            business_path = Path(temp_dir) / "business.json"
            join_path = Path(temp_dir) / "join.json"
            session_state_path = Path(temp_dir) / "session_state.schema.json"
            domain_config_path = Path(temp_dir) / "domain.json"
            atomic_write_text(examples_path, "{bad json\n")
            atomic_write_text(tables_path, "{}\n")
            atomic_write_text(business_path, "{\"entries\": []}\n")
            atomic_write_text(join_path, "{\"patterns\": []}\n")
            atomic_write_text(session_state_path, "{}\n")
            atomic_write_text(domain_config_path, "{}\n")

            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                MetadataRegistry(
                    {
                        "domain_config": domain_config_path,
                        "business_knowledge": business_path,
                        "examples_template": examples_path,
                        "tables_metadata": tables_path,
                        "join_patterns": join_path,
                        "session_state_schema": session_state_path,
                    }
                )


class SessionWorkspaceFailFastTests(unittest.TestCase):
    def test_workspace_raises_when_query_log_lookup_fails(self) -> None:
        service = SessionWorkspaceService(
            session_service=SimpleNamespace(
                get_session=lambda session_id: SimpleNamespace(id=session_id),
                history=lambda session_id: [SimpleNamespace(trace_id="trace_1")],
                resolve_state=lambda session_id: None,
            ),
            runtime_log_repository=SimpleNamespace(
                list_query_logs=lambda limit, session_id: (_ for _ in ()).throw(RuntimeError("boom")),
                get_query_log=lambda trace_id: None,
                get_sql_audit=lambda trace_id: None,
            ),
            audit_service=SimpleNamespace(get_trace=lambda trace_id: None),
            response_restore_service=SimpleNamespace(build_from_trace_id=lambda *args, **kwargs: None),
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            service.get_workspace("sess_1")

    def test_workspace_passes_latest_state_as_session_state_to_restore_service(self) -> None:
        session = ChatSession(id="sess_1")
        message = ChatMessage(id="msg_1", session_id="sess_1", role="user", content="查询库存", trace_id="trace_1")
        state = SessionState(session_id="sess_1")
        query_log = RuntimeQueryLogRecord(trace_id="trace_1", session_id="sess_1", created_at=datetime.utcnow())
        sql_audit = RuntimeSqlAuditRecord(
            sql_audit_id="audit_1",
            trace_id="trace_1",
            context_valid=True,
            sql_valid=True,
            executed=False,
            created_at=datetime.utcnow(),
        )
        trace = TraceRecord(trace_id="trace_1")

        class RestoreService:
            captured_session_state = None

            def build_from_trace_id(
                self,
                trace_id,
                *,
                session_state=None,
                messages=None,
                user_context=None,
                trace=None,
                query_log=None,
                sql_audit=None,
            ):
                self.captured_session_state = session_state
                return ChatResponse(
                    classification=QuestionClassification(question_type="new", subject_domain="unknown"),
                    context_summary=ContextSummary(subject_domain="unknown"),
                    sql=None,
                    context_validation=ValidationResponse(valid=True, errors=[], warnings=[]),
                    sql_validation=ValidationResponse(valid=True, errors=[], warnings=[]),
                    execution=None,
                    next_session_state=session_state or SessionState(session_id="sess_1"),
                )

        restore_service = RestoreService()
        service = SessionWorkspaceService(
            session_service=SimpleNamespace(
                get_session=lambda session_id: session,
                history=lambda session_id: [message],
                resolve_state=lambda session_id: state,
            ),
            runtime_log_repository=SimpleNamespace(
                list_query_logs=lambda limit, session_id: [query_log],
                get_query_log=lambda trace_id: query_log,
                get_sql_audit=lambda trace_id: sql_audit,
            ),
            audit_service=SimpleNamespace(get_trace=lambda trace_id: trace),
            response_restore_service=restore_service,
        )

        workspace = service.get_workspace("sess_1")

        self.assertIs(restore_service.captured_session_state, state)
        self.assertIs(workspace.latest_response.next_session_state, state)


if __name__ == "__main__":
    unittest.main()
