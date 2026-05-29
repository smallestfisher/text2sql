from __future__ import annotations

from collections import OrderedDict
import unittest

from backend.app.models.query_plan import ContextDelta, FilterItem, QueryPlan, SortItem, TimeContext, TimeRange
from backend.app.models.classification import QueryIntent, QuestionClassification
from backend.app.models.intent import StructuredIntent
from backend.app.models.session_state import SessionState
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.intent_service import IntentService
from backend.app.services.llm_client import LLMClient
from backend.app.services.sql_dialect import SqlDialect
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_intent_parser import QueryIntentParser
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.semantic_runtime import SemanticRuntime


class StubRepairLLMClient(LLMClient):
    def __init__(self) -> None:
        self.model_name = "test-model"
        self.api_key = None
        self.api_base = None
        self.timeout_seconds = 20
        self.max_retries = 1
        self.repair_max_retries = 3
        self.cache_ttl_seconds = 300
        self.cache_max_entries = 256
        self._response_cache = OrderedDict()
        self._metrics = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        self.calls += 1
        self._record_metric(task_name, "provider_calls")
        if self.calls < 3:
            return "not valid sql"
        return "SELECT 1 FETCH FIRST 1 ROWS ONLY"

    def _is_single_sql_statement(self, sql: str) -> bool:
        return True


class StubCachedLLMClient(LLMClient):
    def __init__(self) -> None:
        self.model_name = "test-model"
        self.api_key = None
        self.api_base = None
        self.timeout_seconds = 20
        self.max_retries = 1
        self.repair_max_retries = 1
        self.cache_ttl_seconds = 60
        self.cache_max_entries = 4
        self._response_cache = OrderedDict()
        self._metrics = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        self.calls += 1
        self._record_metric(task_name, "provider_calls")
        return '{"subject_domain":"inventory","metrics":["inventory_qty"],"confidence":0.91}'


class StubCachedSqlLLMClient(LLMClient):
    def __init__(self) -> None:
        self.model_name = "test-model"
        self.api_key = None
        self.api_base = None
        self.timeout_seconds = 20
        self.max_retries = 1
        self.repair_max_retries = 1
        self.cache_ttl_seconds = 60
        self.cache_max_entries = 4
        self._response_cache = OrderedDict()
        self._metrics = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        self.calls += 1
        self._record_metric(task_name, "provider_calls")
        return "SELECT product_ID FROM daily_inventory FETCH FIRST 10 ROWS ONLY"

    def _is_single_sql_statement(self, sql: str) -> bool:
        return True


class StubFollowUpXpsIntentLLMClient:
    def __init__(self) -> None:
        self.intent_calls = 0
        self.classification_calls = 0

    def generate_intent(self, prompt_payload, cancellation_token=None):
        self.intent_calls += 1
        return {
            "subject_domain": "plan_actual",
            "metrics": [],
            "entities": [],
            "dimensions": [],
            "filters": ["IS_XPS"],
            "confidence": 0.86,
        }

    def generate_classification_hint(self, prompt_payload, cancellation_token=None):
        self.classification_calls += 1
        return {
            "question_type": "follow_up",
            "subject_domain": "plan_actual",
            "inherit_context": True,
            "confidence": 0.86,
            "context_delta": {
                "add_filters": [
                    {"field": "IS_XPS", "op": "=", "value": "Y"},
                ]
            },
        }

    def check_question_relevance(self, prompt_payload, cancellation_token=None):
        return None


class ConfigDrivenRuntimeRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.domain_config = domain_config
        cls.semantic_runtime = SemanticRuntime(domain_config)


    def test_llm_intent_generation_uses_prompt_cache(self) -> None:
        client = StubCachedLLMClient()
        prompt = {"question": "查询库存", "domain_hints": {"subject_domain": "inventory"}}

        first = client.generate_intent(prompt)
        second = client.generate_intent(prompt)

        self.assertEqual(client.calls, 1)
        self.assertFalse(first.get("cache_hit", False))
        self.assertTrue(second.get("cache_hit"))
        self.assertEqual(second["metrics"], ["inventory_qty"])
        health = client.health()
        self.assertEqual(health["cache_entries"], 1)
        self.assertEqual(health["metrics"]["intent"]["requests"], 2)
        self.assertEqual(health["metrics"]["intent"]["provider_calls"], 1)
        self.assertEqual(health["metrics"]["intent"]["cache_hits"], 1)

    def test_llm_sql_generation_uses_prompt_cache(self) -> None:
        client = StubCachedSqlLLMClient()
        prompt = {"question": "查询库存", "query_contract": {"tables": ["daily_inventory"]}}

        first = client.generate_sql_hint(prompt)
        second = client.generate_sql_hint(prompt)

        self.assertEqual(client.calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(second, "SELECT product_ID FROM daily_inventory FETCH FIRST 10 ROWS ONLY;")
        health = client.health()
        self.assertEqual(health["cache_entries"], 1)
        self.assertEqual(health["metrics"]["sql"]["requests"], 2)
        self.assertEqual(health["metrics"]["sql"]["provider_calls"], 1)
        self.assertEqual(health["metrics"]["sql"]["cache_hits"], 1)

    def test_llm_cache_can_be_disabled(self) -> None:
        client = StubCachedLLMClient()
        client.cache_ttl_seconds = 0
        prompt = {"question": "查询库存"}

        client.generate_intent(prompt)
        client.generate_intent(prompt)

        self.assertEqual(client.calls, 2)
        health = client.health()
        self.assertEqual(health["cache_entries"], 0)
        self.assertEqual(health["metrics"]["intent"]["requests"], 2)
        self.assertEqual(health["metrics"]["intent"]["provider_calls"], 2)
        self.assertEqual(health["metrics"]["intent"]["cache_hits"], 0)

    def test_repair_sql_uses_dedicated_retry_budget(self) -> None:
        client = StubRepairLLMClient()

        repaired_sql = client.repair_sql(
            prompt_payload={"query_plan": {"tables": ["demo_table"]}},
            sql="SELECT 1;",
            errors=["sql is missing limit"],
            warnings=[],
        )

        self.assertEqual(client.calls, 3)
        self.assertEqual(repaired_sql, "SELECT 1 FETCH FIRST 1 ROWS ONLY;")
        health = client.health()
        self.assertEqual(health["repair_max_retries"], 3)
        self.assertEqual(health["metrics"]["repair"]["requests"], 1)
        self.assertEqual(health["metrics"]["repair"]["provider_calls"], 3)

    def test_inventory_explicit_source_group_is_profile_driven(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["daily_inventory", "oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertEqual(sanitized.tables[0], "oms_inventory")
        self.assertNotIn("daily_inventory", sanitized.tables[1:])

    def test_demand_support_table_and_post_process_rules_are_profile_driven(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="demand",
            metrics=["product_count"],
            tables=["p_demand"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="source_table", op="=", value="p_demand"),
                FilterItem(field="demand_month", op="=", value="202604"),
            ],
            sort=[SortItem(field="biz_month", order="desc")],
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertIn("product_attributes", sanitized.tables)
        self.assertEqual(sanitized.dimensions, [])
        self.assertEqual(sanitized.sort, [])
        self.assertIn(FilterItem(field="demand_qty", op=">", value=0), sanitized.filters)

    def test_plan_actual_support_table_rule_appends_product_attributes(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            dimensions=["common_categories"],
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertIn("product_attributes", sanitized.tables)

    def test_parser_resolves_array_oxide_monthly_input_volume(self) -> None:
        question = "2026年Array工厂Oxide类产品，每个月分别投入多少物量"
        intent = QueryIntentParser(self.domain_config, self.semantic_runtime).parse(question)

        self.assertEqual(intent.subject_domain, "plan_actual")
        self.assertEqual(intent.matched_metrics, ["actual_input_qty"])
        self.assertEqual(intent.requested_dimensions, ["biz_month"])
        self.assertIn(FilterItem(field="factory", op="=", value="ARRAY"), intent.filters)
        self.assertIn(FilterItem(field="act_type", op="=", value="投入"), intent.filters)
        self.assertIn(FilterItem(field="IS_OXIDE", op="=", value="Y"), intent.filters)

        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        llm_client = StubFollowUpXpsIntentLLMClient()
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            intent_service=IntentService(llm_client, prompt_builder),
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        query_plan = planner.build_plan_from_intent(
            classification=QuestionClassification(
                question_type="new",
                subject_domain=intent.subject_domain,
                confidence=1.0,
            ),
            query_intent=intent,
        )

        self.assertEqual(query_plan.metrics, ["actual_input_qty"])
        self.assertEqual(query_plan.dimensions, ["biz_month"])
        self.assertEqual(query_plan.tables, ["production_actuals", "product_attributes"])
        self.assertIn("production_actuals.product_ID = product_attributes.product_ID", query_plan.join_path)

    def test_llm_filter_string_follow_up_replaces_product_attribute_flag(self) -> None:
        llm_client = StubFollowUpXpsIntentLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            intent_service=IntentService(llm_client, prompt_builder),
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_xps",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="factory", op="=", value="ARRAY"),
                FilterItem(field="act_type", op="=", value="投入"),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
            ],
        )

        trace = planner.build_planning_trace(
            question="XPS呢",
            session_state=session_state,
        )
        query_plan = planner.build_plan_from_intent(
            classification=trace["classification"],
            query_intent=trace["query_intent"],
            session_state=session_state,
        )

        self.assertEqual(trace["llm_intent"]["status"], "completed")
        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertEqual(llm_client.classification_calls, 1)
        self.assertIn(FilterItem(field="IS_XPS", op="=", value="Y"), query_plan.filters)
        self.assertNotIn(FilterItem(field="IS_OXIDE", op="=", value="Y"), query_plan.filters)
        self.assertIn(FilterItem(field="factory", op="=", value="ARRAY"), query_plan.filters)
        self.assertIn(FilterItem(field="act_type", op="=", value="投入"), query_plan.filters)
        self.assertIn("product_attributes", query_plan.tables)

    def test_merge_context_delta_replaces_time_and_factory_filters(self) -> None:
        session_state = SessionState(
            session_id="sess_context",
            subject_domain="plan_actual",
            tables=["production_actuals"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="factory", op="=", value="ARRAY"),
                FilterItem(field="act_type", op="=", value="投入"),
                FilterItem(field="biz_month", op="=", value="202602"),
            ],
        )
        query_intent = QueryIntent(
            normalized_question="3月mdl呢",
            filters=[
                FilterItem(field="factory", op="=", value="MDL"),
                FilterItem(field="biz_month", op="=", value="202603"),
            ],
            time_context=TimeContext(
                grain="month",
                range=TimeRange(start="2026-03-01", end="2026-03-31"),
            ),
            subject_domain="unknown",
            has_follow_up_cue=True,
            has_explicit_slots=True,
        )

        merged = self.semantic_runtime.merge_with_session(
            session_state=session_state,
            query_intent=query_intent,
            context_delta=ContextDelta(
                add_filters=query_intent.filters,
                replace_time_context=query_intent.time_context,
            ),
        )

        self.assertIn(FilterItem(field="factory", op="=", value="MDL"), merged.filters)
        self.assertNotIn(FilterItem(field="factory", op="=", value="ARRAY"), merged.filters)
        self.assertIn(FilterItem(field="biz_month", op="=", value="202603"), merged.filters)
        self.assertNotIn(FilterItem(field="biz_month", op="=", value="202602"), merged.filters)
        self.assertIn(FilterItem(field="act_type", op="=", value="投入"), merged.filters)

    def test_intent_service_drops_bad_llm_filters_without_failing(self) -> None:
        class BadFilterIntentLLMClient:
            def generate_intent(self, prompt_payload, cancellation_token=None):
                return {
                    "subject_domain": "plan_actual",
                    "metrics": ["actual_input_qty"],
                    "filters": ["IS_NOT_A_REAL_FIELD", 7],
                    "confidence": 0.7,
                }

        llm_client = BadFilterIntentLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        service = IntentService(llm_client, prompt_builder)

        result = service.generate_intent(
            question="XPS呢",
            query_intent=QueryIntent(
                normalized_question="xps呢",
                subject_domain="unknown",
                has_follow_up_cue=True,
                has_explicit_slots=False,
            ),
            session_state=SessionState(
                session_id="sess_bad_filter",
                subject_domain="plan_actual",
                tables=["production_actuals", "product_attributes"],
                metrics=["actual_input_qty"],
                filters=[FilterItem(field="IS_OXIDE", op="=", value="Y")],
            ),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent"].subject_domain, "plan_actual")
        self.assertEqual(result["intent"].filters, [])
        self.assertIn("coercion_warnings", result["intent"].raw_payload)

    def test_normalizer_drops_output_metric_when_act_type_is_input(self) -> None:
        normalizer = IntentNormalizer(self.semantic_runtime)
        self.assertEqual(self.semantic_runtime.metric_act_type_scope("actual_output_qty"), "产出")
        self.assertEqual(self.semantic_runtime.metric_act_type_scope("actual_input_qty"), "投入")
        intent = StructuredIntent(
            source="llm",
            normalized_question="2026年array工厂每个月投入多少物量",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            filters=[FilterItem(field="act_type", op="=", value="投入")],
        )

        normalized = normalizer.normalize(intent, question=intent.normalized_question)["intent"]

        self.assertEqual(normalized.metrics, ["actual_input_qty"])


if __name__ == "__main__":
    unittest.main()
