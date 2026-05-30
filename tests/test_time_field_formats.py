from __future__ import annotations

import unittest

from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_validator import SqlValidator


class TimeFieldFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.domain_config = DomainConfigLoader().load()
        cls.semantic_runtime = SemanticRuntime(cls.domain_config)
        cls.prompt_builder = PromptBuilder(semantic_runtime=cls.semantic_runtime)
        cls.sql_validator = SqlValidator(semantic_runtime=cls.semantic_runtime)

    def test_semantic_runtime_exposes_time_field_metadata(self) -> None:
        metadata = self.semantic_runtime.table_time_field("production_actuals", "work_date")

        self.assertEqual(metadata, {"grain": "day", "format": "YYYYMMDD"})

        candidates = self.semantic_runtime.resolve_time_field_candidates(
            "plan_actual",
            ["production_actuals"],
            "biz_date",
        )
        self.assertEqual(candidates, [])

    def test_plan_output_fields_follow_tables_metadata(self) -> None:
        self.assertIn("target_Out_glass_qty", self.semantic_runtime.table_fields("daily_PLAN"))
        self.assertIn("target_Out_TTL_panel_qty", self.semantic_runtime.table_fields("monthly_plan_approved"))

        resolved_fields = self.semantic_runtime.resolve_field_candidates(
            "plan_actual",
            ["daily_PLAN", "monthly_plan_approved"],
            "plan_output_total_panel_qty",
        )

        self.assertEqual(resolved_fields, {"plan_output_total_panel_qty"})

    def test_sql_prompt_emits_format_driven_time_resolution(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
        )

        prompt = self.prompt_builder.build_sql_prompt(
            query_plan,
            question="2026年4月实际产出",
        )

        self.assertEqual(prompt["planning_context"]["time_resolution"], {})
        self.assertEqual(prompt["planning_context"]["output_shape"]["logical_dimension_examples"], {})

    def test_sql_validator_warns_incompatible_time_literals(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            filters=[FilterItem(field="biz_date", op="=", value="2026-04-01")],
            limit=10,
        )

        result = self.sql_validator.validate_detailed(
            "SELECT SUM(GLS_qty) FROM production_actuals WHERE work_date = '2026-04-01' LIMIT 10;",
            self.domain_config,
            query_plan=query_plan,
        )

        self.assertEqual([], result.errors)
        self.assertIn("sql does not cover all query plan filters: biz_date", result.warnings)

    def test_sql_validator_warns_month_filter_collapsed_to_single_day(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
            limit=10,
        )

        result = self.sql_validator.validate_detailed(
            "SELECT SUM(GLS_qty) FROM production_actuals WHERE work_date = '20260401' LIMIT 10;",
            self.domain_config,
            query_plan=query_plan,
        )

        self.assertEqual([], result.errors)
        self.assertIn("sql does not cover all query plan filters: biz_month", result.warnings)


if __name__ == "__main__":
    unittest.main()
