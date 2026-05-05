from __future__ import annotations

import unittest

from backend.app.models.query_plan import FilterItem, QueryPlan, SortItem
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.llm_client import LLMClient
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


if __name__ == "__main__":
    unittest.main()
