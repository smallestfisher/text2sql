from __future__ import annotations

import unittest

from backend.app.models.classification import QueryIntent
from backend.app.models.intent import StructuredIntent
from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.intent_service import IntentService
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime


QUESTION = "最新OMS库存，TtL物量，对应库龄分布情况，库龄分为<3M、3-6M、6-12M、>12M"


class InventoryAgeDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.semantic_runtime = SemanticRuntime(domain_config)
        cls.prompt_builder = PromptBuilder(semantic_runtime=cls.semantic_runtime)
        cls.intent_normalizer = IntentNormalizer(cls.semantic_runtime)
        cls.intent_service = IntentService(LLMClient(), cls.prompt_builder)

    def test_intent_normalizer_preserves_distribution_mode(self) -> None:
        intent = StructuredIntent(
            source="llm",
            normalized_question=QUESTION.lower(),
            subject_domain="inventory",
            metrics=["inventory_qty"],
            dimensions=["common_categories"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
            analysis_mode="distribution",
        )

        normalized = self.intent_normalizer.normalize(intent, question=QUESTION)

        self.assertEqual(normalized["intent"].analysis_mode, "distribution")

    def test_intent_prompt_contains_generic_dimension_and_latest_guidance(self) -> None:
        query_intent = QueryIntent(
            normalized_question=QUESTION.lower(),
            matched_metrics=["inventory_qty"],
            matched_entities=[],
            requested_dimensions=[],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
            subject_domain="inventory",
            analysis_mode="distribution",
            has_explicit_slots=True,
        )

        prompt = self.prompt_builder.build_intent_prompt(
            question=QUESTION,
            query_intent=query_intent,
            session_state=None,
        )
        constraints = "\n".join(prompt["instructions"]["constraints"])
        business_knowledge = prompt["business_knowledge"]

        self.assertIn("不要因为“分布”“情况”“统计”就自行补", constraints)
        self.assertIn("biz_month", constraints)
        self.assertIn("不要自行追加 common_categories", business_knowledge)
        self.assertIn("默认取 oms_inventory.report_month 的最新月份", business_knowledge)
        self.assertIn("<3M = ONE_AGE_panel_qty + TWO_AGE_panel_qty + THREE_AGE_panel_qty", business_knowledge)

    def test_sql_prompt_contains_generic_latest_n_guidance_and_age_bucket_knowledge(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[
                FilterItem(field="source_table", op="=", value="oms_inventory"),
                FilterItem(
                    field="biz_month",
                    op="latest_n",
                    value={"count": 1, "source_table": "oms_inventory"},
                ),
            ],
            analysis_mode="distribution",
        )

        prompt = self.prompt_builder.build_sql_prompt(query_plan, question=QUESTION)
        preferences = "\n".join(prompt["instructions"]["sql_preferences"])
        business_notes = prompt["business_notes"]

        self.assertIn("latest_n", preferences)
        self.assertIn("MAX(真实排序字段)", preferences)
        self.assertIn("不要自行追加 common_categories", business_notes)
        self.assertIn(">12M = SIX_AGE_panel_qty + SEVEN_AGE_panel_qty + EUGHT_AGE_panel_qty", business_notes)

    def test_example_library_contains_real_oms_age_distribution_case(self) -> None:
        examples = self.prompt_builder._load_examples()

        self.assertIn("inventory_oms_latest_ttl_age_distribution_001", examples)

    def test_distribution_query_plan_does_not_inject_default_sort(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
            analysis_mode="distribution",
            dimensions=[],
            sort=[],
            limit=200,
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertEqual(sanitized.sort, [])


if __name__ == "__main__":
    unittest.main()
