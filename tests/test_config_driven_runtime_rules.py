from __future__ import annotations

import unittest

from backend.app.models.query_plan import FilterItem, QueryPlan, SortItem
from backend.app.models.classification import QuestionClassification
from backend.app.models.intent import StructuredIntent
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.intent_service import IntentService
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_intent_parser import QueryIntentParser
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.semantic_runtime import SemanticRuntime


class StubRepairLLMClient(LLMClient):
    def __init__(self) -> None:
        super().__init__(max_retries=1, repair_max_retries=3)
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict]) -> str:
        self.calls += 1
        if self.calls < 3:
            return "not valid sql"
        return "SELECT 1 LIMIT 1"


class ConfigDrivenRuntimeRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.domain_config = domain_config
        cls.semantic_runtime = SemanticRuntime(domain_config)

    def test_repair_sql_uses_dedicated_retry_budget(self) -> None:
        client = StubRepairLLMClient()

        repaired_sql = client.repair_sql(
            prompt_payload={"query_plan": {"tables": ["demo_table"]}},
            sql="SELECT 1;",
            errors=["sql is missing limit"],
            warnings=[],
        )

        self.assertEqual(client.calls, 3)
        self.assertEqual(repaired_sql, "SELECT 1 LIMIT 1;")
        self.assertEqual(client.health()["repair_max_retries"], 3)

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
        llm_client = LLMClient()
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
