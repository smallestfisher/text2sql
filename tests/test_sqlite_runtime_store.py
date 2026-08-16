from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.app.models.evaluation import EvaluationCase
from backend.app.repositories.db_evaluation_case_repository import DbEvaluationCaseRepository
from backend.app.repositories.file_vector_document_repository import FileVectorDocumentRepository
from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.runtime_store_initializer import RuntimeStoreInitializer


class SqliteRuntimeStoreTests(unittest.TestCase):
    def test_schema_is_repeatable_and_uses_wal_without_config_or_vectors(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "runtime.db"
            connector = DatabaseConnector(
                database_url=f"sqlite:///{database_path}",
                sql_dialect="sqlite",
            )
            try:
                RuntimeStoreInitializer(connector).ensure_schema()
                RuntimeStoreInitializer(connector).ensure_schema()
                table_rows = connector.fetch_all(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
                index_rows = connector.fetch_all(
                    "SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name"
                )
                journal_mode = connector.fetch_one("PRAGMA journal_mode")
            finally:
                connector.dispose()

        table_names = {str(row["name"]) for row in table_rows}
        self.assertIn("semantic_releases", table_names)
        self.assertIn("chat_sessions", table_names)
        self.assertIn("evaluation_cases", table_names)
        self.assertNotIn("semantic_assets", table_names)
        self.assertNotIn("app_config", table_names)
        self.assertNotIn("vector_corpus_documents", table_names)
        self.assertIn(
            "idx_query_logs_session_created",
            {str(row["name"]) for row in index_rows},
        )
        self.assertEqual(str(journal_mode["journal_mode"]).lower(), "wal")

    def test_evaluation_cases_round_trip_in_runtime_store(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "runtime.db"
            connector = DatabaseConnector(
                database_url=f"sqlite:///{database_path}",
                sql_dialect="sqlite",
            )
            try:
                RuntimeStoreInitializer(connector).ensure_schema()
                repository = DbEvaluationCaseRepository(connector)
                case = EvaluationCase(
                    id="case_1",
                    question="按类别汇总最近一个周期的目标指标",
                    expected_domain="example_domain",
                    expected_metrics=["example_metric"],
                )

                repository.create(case)

                self.assertEqual(repository.get("case_1"), case)
                self.assertEqual(repository.list_cases(), [case])
            finally:
                connector.dispose()

    def test_file_vector_cache_round_trips_and_removes_scope_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            repository = FileVectorDocumentRepository(Path(temporary_directory))
            now = datetime.now(tz=timezone.utc)
            document = {
                "document_id": "doc_1",
                "scope_id": "release_1",
                "source_type": "table",
                "source_id": "orders",
                "summary": "Orders",
                "text_content": "Orders table",
                "metadata": {"domain": "sales"},
                "content_hash": "hash_1",
                "embedding_provider": "openai",
                "embedding_backend": "api",
                "embedding_model": "embedding-model",
                "embedding_dimensions": 2,
                "vector": [0.1, 0.2],
                "created_at": now,
                "updated_at": now,
            }

            self.assertEqual(repository.upsert_documents([document]), 1)
            loaded = repository.find_by_document_ids("release_1", ["doc_1"])
            self.assertEqual(loaded[0]["vector"], [0.1, 0.2])
            self.assertEqual(loaded[0]["metadata"], {"domain": "sales"})
            self.assertIsInstance(loaded[0]["created_at"], datetime)
            self.assertEqual(repository.delete_missing("release_1", []), 1)
            self.assertEqual(list(Path(temporary_directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
