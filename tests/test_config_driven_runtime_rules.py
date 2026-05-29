from __future__ import annotations

from collections import OrderedDict
import unittest

from backend.app.models.query_plan import FilterItem, QueryPlan, SortItem
from backend.app.models.classification import QueryIntent, QuestionClassification
from backend.app.models.intent import StructuredIntent
from backend.app.models.session_state import SessionState
from backend.app.models.semantic_bundle import SemanticBundle
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.llm_client import LLMClient
from backend.app.services.sql_dialect import SqlDialect
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_intent_parser import QueryIntentParser
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.semantic_bundle_service import SemanticBundleService
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.session_state_service import SessionStateService


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
        return '{"decision":"answerable","subject_domain":"inventory","semantic_brief":"查询库存数量。","contract_hint":{"metrics":["inventory_qty"]}}'


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


class StubFollowUpXpsSemanticBundleLLMClient:
    def __init__(self) -> None:
        self.enabled = True
        self.rewrite_calls = 0

    def generate_semantic_bundle(self, prompt_payload, cancellation_token=None):
        self.rewrite_calls += 1
        question = prompt_payload.get("original_question", "")
        if question == "XPS呢":
            return {
                "context_decision": "follow_up",
                "effective_question": "2026年Array工厂XPS类产品，每个月分别投入多少物量",
                "decision": "answerable",
                "subject_domain": "plan_actual",
                "user_intent": "查询2026年Array工厂XPS类产品每个月投入物量。",
                "semantic_brief": "查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。",
                "knowledge_brief": "XPS类产品属性可结合product_attributes中的IS_XPS字段理解。",
                "contract_hint": {
                    "metrics": ["actual_input_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [
                        {"field": "factory", "op": "=", "value": "ARRAY"},
                        {"field": "act_type", "op": "=", "value": "投入"},
                        {"field": "IS_XPS", "op": "=", "value": "Y"},
                    ],
                },
            }
        if question == "2025年呢":
            return {
                "context_decision": "follow_up",
                "effective_question": "2025年Array工厂Oxide类产品，每个月分别投入多少物量",
                "decision": "answerable",
                "subject_domain": "plan_actual",
                "user_intent": "查询2025年Array工厂Oxide类产品每个月投入物量。",
                "semantic_brief": "查询2025年Array工厂Oxide类产品的实际投入物量，按月份展示。",
                "contract_hint": {},
            }
        effective_question = prompt_payload.get("effective_question") or question
        return {
            "context_decision": "new",
            "effective_question": effective_question,
            "decision": "answerable",
            "subject_domain": "plan_actual",
            "user_intent": effective_question,
            "semantic_brief": effective_question,
            "contract_hint": {},
        }


class StubDemandCalculationSemanticBundleLLMClient:
    enabled = True

    def generate_semantic_bundle(self, prompt_payload, cancellation_token=None):
        return {
            "context_decision": "new",
            "effective_question": prompt_payload.get("effective_question"),
            "decision": "answerable",
            "subject_domain": "demand",
            "user_intent": "计算202605月V到P GLASS转换率。",
            "semantic_brief": "计算202605月V到P GLASS转换率：V侧使用202604月份第一版需求，P侧使用202604月份最后一版需求。",
            "knowledge_brief": "需求表是横向月字段结构，目标需求月份需要由base MONTH和offset列映射。",
            "calculation_contract": {
                "type": "demand_v_to_p_glass_conversion_rate",
                "formula": "p_glass_demand_qty / v_glass_demand_qty",
                "target_demand_month": "202605",
                "join_keys": ["FGCODE"],
                "horizontal_month_mapping": {
                    "base_month_field": "MONTH",
                    "target_month_field": "demand_month",
                    "offset_columns": {
                        "0": "REQUIREMENT_QTY",
                        "1": "NEXT_REQUIREMENT",
                        "2": "LAST_REQUIREMENT",
                        "3": "MONTH4",
                        "4": "MONTH5",
                        "5": "MONTH6",
                        "6": "MONTH7",
                    },
                },
                "sources": [
                    {
                        "alias": "v",
                        "table": "v_demand",
                        "base_month": "202604",
                        "version_field": "PM_VERSION",
                        "version_selector": "first",
                        "value_alias": "v_glass_demand_qty",
                    },
                    {
                        "alias": "p",
                        "table": "p_demand",
                        "base_month": "202604",
                        "version_field": "PM_VERSION",
                        "version_selector": "last",
                        "value_alias": "p_glass_demand_qty",
                    },
                ],
            },
            "contract_hint": {
                "entities": ["plan_time"],
                "dimensions": [],
                "filters": [],
                "time_context": {"grain": "month", "range": {"start": "202605", "end": "202605"}},
                "analysis_mode": "summary",
            },
        }

class ConfigDrivenRuntimeRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.domain_config = domain_config
        cls.semantic_runtime = SemanticRuntime(domain_config)


    def test_llm_semantic_bundle_generation_uses_prompt_cache(self) -> None:
        client = StubCachedLLMClient()
        prompt = {"original_question": "查询库存", "knowledge_context": {"supported_domains": ["inventory"]}}

        first = client.generate_semantic_bundle(prompt)
        second = client.generate_semantic_bundle(prompt)

        self.assertEqual(client.calls, 1)
        self.assertFalse(first.get("cache_hit", False))
        self.assertTrue(second.get("cache_hit"))
        self.assertEqual(second["contract_hint"]["metrics"], ["inventory_qty"])
        health = client.health()
        self.assertEqual(health["cache_entries"], 1)
        self.assertEqual(health["metrics"]["semantic_bundle"]["requests"], 2)
        self.assertEqual(health["metrics"]["semantic_bundle"]["provider_calls"], 1)
        self.assertEqual(health["metrics"]["semantic_bundle"]["cache_hits"], 1)

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

        client.generate_semantic_bundle(prompt)
        client.generate_semantic_bundle(prompt)

        self.assertEqual(client.calls, 2)
        health = client.health()
        self.assertEqual(health["cache_entries"], 0)
        self.assertEqual(health["metrics"]["semantic_bundle"]["requests"], 2)
        self.assertEqual(health["metrics"]["semantic_bundle"]["provider_calls"], 2)
        self.assertEqual(health["metrics"]["semantic_bundle"].get("cache_hits", 0), 0)

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
        llm_client = StubFollowUpXpsSemanticBundleLLMClient()
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
            semantic_bundle_service=SemanticBundleService(llm_client, prompt_builder),
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
        llm_client = StubFollowUpXpsSemanticBundleLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
            semantic_bundle_service=SemanticBundleService(llm_client, prompt_builder),
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
            semantic_bundle=trace["semantic_bundle"],
        )

        self.assertEqual(trace["contract_compilation"]["status"], "completed")
        self.assertEqual(trace["effective_question"], "2026年Array工厂XPS类产品，每个月分别投入多少物量")
        self.assertEqual(trace["semantic_bundle"].context_decision, "follow_up")
        self.assertEqual(trace["semantic_bundle"].semantic_brief, "查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。")
        self.assertEqual(llm_client.rewrite_calls, 1)
        self.assertEqual(trace["classification"].question_type, "new")
        self.assertFalse(trace["classification"].inherit_context)
        self.assertIn(FilterItem(field="IS_XPS", op="=", value="Y"), query_plan.filters)
        self.assertEqual(query_plan.semantic_brief, trace["semantic_bundle"].semantic_brief)
        self.assertNotIn(FilterItem(field="IS_OXIDE", op="=", value="Y"), query_plan.filters)
        self.assertIn(FilterItem(field="factory", op="=", value="ARRAY"), query_plan.filters)
        self.assertIn(FilterItem(field="act_type", op="=", value="投入"), query_plan.filters)
        self.assertIn("product_attributes", query_plan.tables)

    def test_semantic_bundle_can_replace_year_in_follow_up(self) -> None:
        llm_client = StubFollowUpXpsSemanticBundleLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_year",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="biz_date", op="between", value=["2026-01-01", "2026-12-31"]),
                FilterItem(field="factory", op="=", value="ARRAY"),
                FilterItem(field="act_type", op="=", value="投入"),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
            ],
        )

        trace = planner.build_planning_trace(
            question="2025年呢",
            session_state=session_state,
        )
        query_plan = planner.build_plan_from_intent(
            classification=trace["classification"],
            query_intent=trace["query_intent"],
            session_state=session_state,
        )

        self.assertEqual(trace["effective_question"], "2025年Array工厂Oxide类产品，每个月分别投入多少物量")
        self.assertEqual(trace["semantic_bundle"].context_decision, "follow_up")
        self.assertEqual(trace["classification"].question_type, "new")
        self.assertFalse(trace["classification"].inherit_context)
        self.assertIn(FilterItem(field="biz_date", op="between", value=["2025-01-01", "2025-12-31"]), query_plan.filters)
        self.assertNotIn(FilterItem(field="biz_date", op="between", value=["2026-01-01", "2026-12-31"]), query_plan.filters)
        self.assertIn(FilterItem(field="IS_OXIDE", op="=", value="Y"), query_plan.filters)

    def test_semantic_bundle_calculation_contract_drives_demand_composite_query(self) -> None:
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=StubDemandCalculationSemanticBundleLLMClient(),
            prompt_builder=prompt_builder,
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        question = "请计算202605月的V到P GLASS转换率，V选择202604月份的第一版需求，P选择202604的最后一版需求"

        trace = planner.build_planning_trace(question=question)
        query_plan = planner.build_plan_from_intent(
            classification=trace["classification"],
            query_intent=trace["query_intent"],
            semantic_bundle=trace["semantic_bundle"],
        )

        self.assertEqual(query_plan.question_type, "new")
        self.assertFalse(query_plan.need_clarification)
        self.assertEqual(query_plan.subject_domain, "demand")
        self.assertEqual(query_plan.tables, ["v_demand", "p_demand"])
        self.assertEqual(query_plan.metrics, [])
        self.assertEqual(query_plan.calculation_contract["type"], "demand_v_to_p_glass_conversion_rate")
        self.assertEqual(query_plan.calculation_contract["target_demand_month"], "202605")
        self.assertEqual(query_plan.calculation_contract["sources"][0]["version_selector"], "first")
        self.assertEqual(query_plan.calculation_contract["sources"][1]["version_selector"], "last")

        sql_prompt = prompt_builder.build_sql_prompt(query_plan, question=question)
        self.assertEqual(sql_prompt["query_contract"]["calculation_contract"], query_plan.calculation_contract)
        schema_text = " ".join(sql_prompt["table_schemas"]["v_demand"]["columns"])
        self.assertIn("REQUIREMENT_QTY", schema_text)
        self.assertIn("NEXT_REQUIREMENT", schema_text)
        self.assertIn("MONTH7", schema_text)
        preference_text = " ".join(sql_prompt["instructions"]["sql_preferences"])
        self.assertIn("calculation_contract", preference_text)
        self.assertNotIn("展开成 demand_month/demand_qty", preference_text)
        self.assertIn("目标需求月份不能简单理解成 base MONTH 本身", sql_prompt["business_notes"])
        self.assertIn("需求月份映射规则", sql_prompt["business_notes"])

    def test_session_state_stores_semantic_brief_in_recent_turns(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="IS_XPS", op="=", value="Y")],
            semantic_brief="查询XPS类产品的actual_input_qty，并按biz_month展示。",
            calculation_contract={"type": "example", "formula": "a / b"},
        )

        state = SessionStateService().build_next_state(
            query_plan=query_plan,
            previous_state=None,
            question="2026年Array工厂XPS类产品，每个月分别投入多少物量",
        )

        self.assertEqual(state.last_semantic_brief, query_plan.semantic_brief)
        self.assertEqual(state.recent_turns[-1].semantic_brief, query_plan.semantic_brief)
        self.assertEqual(state.recent_turns[-1].query_contract["semantic_brief"], query_plan.semantic_brief)
        self.assertEqual(state.recent_turns[-1].query_contract["calculation_contract"], query_plan.calculation_contract)

    def test_semantic_bundle_contract_ignores_non_object_filters_without_guessing(self) -> None:
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=StubFollowUpXpsSemanticBundleLLMClient(),
            prompt_builder=prompt_builder,
            intent_normalizer=IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        semantic_bundle = SemanticBundle(
            original_question="XPS呢",
            effective_question="2026年Array工厂XPS类产品，每个月分别投入多少物量",
            subject_domain="plan_actual",
            semantic_brief="查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。",
            contract_hint={
                "metrics": ["actual_input_qty"],
                "dimensions": ["biz_month"],
                "filters": [
                    "IS_XPS",
                    7,
                    {"field": "factory", "op": "=", "value": "ARRAY"},
                ],
            },
        )

        query_plan = planner.build_plan_from_intent(
            classification=QuestionClassification(question_type="new", subject_domain="plan_actual"),
            query_intent=QueryIntent(normalized_question="xps呢", subject_domain="plan_actual"),
            semantic_bundle=semantic_bundle,
        )

        self.assertEqual(query_plan.filters, [FilterItem(field="factory", op="=", value="ARRAY")])

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
