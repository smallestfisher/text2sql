from __future__ import annotations

from datetime import datetime
import unittest

from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.services.runtime_store_initializer import RuntimeStoreInitializer


class FakeRuntimeConnector:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.writes: list[tuple[str, dict | None]] = []

    def fetch_all(self, sql: str, params: dict | None = None) -> list[dict]:
        return self.rows

    def execute_write(self, sql: str, params: dict | None = None) -> int:
        self.writes.append((sql, params))
        return 1


class FakeMigrationConnector:
    def __init__(self, existing_columns: set[tuple[str, str]]) -> None:
        self.existing_columns = existing_columns
        self.writes: list[str] = []

    def fetch_one(self, sql: str, params: dict | None = None) -> dict | None:
        assert params is not None
        table_name = str(params["table_name"])
        column_name = str(params["column_name"])
        if (table_name, column_name) in self.existing_columns:
            return {"COLUMN_NAME": column_name}
        return None

    def execute_write(self, sql: str, params: dict | None = None) -> int:
        self.writes.append(sql)
        return 1


class RuntimeStoreInitializerTests(unittest.TestCase):
    def test_drops_obsolete_plan_columns(self) -> None:
        connector = FakeMigrationConnector(
            {
                ("query_logs", "plan_valid"),
                ("sql_audit_logs", "plan_valid"),
            }
        )
        initializer = RuntimeStoreInitializer(connector)  # type: ignore[arg-type]
        errors: list[str] = []

        initializer._drop_column_if_exists("query_logs", "plan_valid", errors)
        initializer._drop_column_if_exists("sql_audit_logs", "plan_valid", errors)
        initializer._drop_column_if_exists("query_logs", "plan_risk_level", errors)

        self.assertEqual(errors, [])
        self.assertEqual(
            connector.writes,
            [
                "ALTER TABLE query_logs DROP COLUMN plan_valid",
                "ALTER TABLE sql_audit_logs DROP COLUMN plan_valid",
            ],
        )


def test_runtime_query_log_hydrates_question_context_fields() -> None:
    repository = DbRuntimeLogRepository(FakeRuntimeConnector())

    record = repository._hydrate_query_log(
        {
            "trace_id": "trace_1",
            "session_id": "session_1",
            "user_id": "user_1",
            "question": "split it by region",
            "effective_question": "query March 2024 sales amount by brand and region",
            "context_relation": "follow_up",
            "question_decision": "answerable",
            "conversation_summary": "The previous turn queried March 2024 sales by brand.",
            "semantic_brief": "The user wants to add region as a dimension.",
            "question_context_json": '{"source":"llm"}',
            "question_type": "follow_up",
            "subject_domain": "sales_financial",
            "answer_status": "ok",
            "context_valid": True,
            "context_risk_level": "low",
            "context_risk_flags_json": "[]",
            "sql_valid": True,
            "sql_risk_level": "low",
            "sql_risk_flags_json": "[]",
            "executed": True,
            "row_count": 3,
            "warnings_json": "[]",
            "trace_json": "{}",
            "created_at": datetime.utcnow(),
        }
    )

    assert record.effective_question == "query March 2024 sales amount by brand and region"
    assert record.context_relation == "follow_up"
    assert record.question_decision == "answerable"
    assert record.conversation_summary == "The previous turn queried March 2024 sales by brand."
    assert record.semantic_brief == "The user wants to add region as a dimension."
    assert record.question_context == {"source": "llm"}


def test_runtime_query_log_extracts_total_elapsed_ms_from_trace() -> None:
    repository = DbRuntimeLogRepository(FakeRuntimeConnector())

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

    assert record.total_elapsed_ms == 1532
    assert record.prompt_context_summary == {}


def test_runtime_retrieval_log_round_trips_new_fields() -> None:
    connector = FakeRuntimeConnector(
        [
            {
                "retrieval_log_id": "rl_1",
                "trace_id": "trace_1",
                "rank_position": 1,
                "source_type": "example",
                "source_id": "ex_1",
                "summary": "sales by region example",
                "retrieval_channel": "hybrid",
                "source_score": 0.82,
                "score": 0.91,
                "matched_features_json": '["sales"]',
                "metadata_json": '{"retrieval_channel":"hybrid"}',
                "created_at": datetime.utcnow(),
            }
        ]
    )
    repository = DbRuntimeLogRepository(connector)

    records = repository.list_retrieval_logs("trace_1")

    assert records[0].summary == "sales by region example"
    assert records[0].retrieval_channel == "hybrid"
    assert records[0].source_score == 0.82

    repository.log_retrieval(
        "trace_2",
        RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="knowledge",
                    source_id="kb_1",
                    score=0.7,
                    summary="default sales amount definition",
                    retrieval_channel="vector",
                    source_score=0.66,
                )
            ]
        ),
    )

    insert_params = connector.writes[-1][1]
    assert insert_params is not None
    assert insert_params["summary"] == "default sales amount definition"
    assert insert_params["retrieval_channel"] == "vector"
    assert insert_params["source_score"] == 0.66
