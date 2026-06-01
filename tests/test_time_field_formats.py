from __future__ import annotations

import unittest

from backend.app.models.semantic_types import FilterItem
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_validator import SqlValidator


def sql_context(sql_context_value: SqlGenerationContext) -> SqlGenerationContext:
    return SqlGenerationContext(**sql_context_value.model_dump(mode="python"))


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
        self.assertTrue(candidates)

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
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="2026年4月实际产出",
        )

        self.assertIn("biz_month", prompt["evidence_context"]["time_resolution"])
        self.assertNotIn("output_shape", prompt["evidence_context"])

    def test_sql_validator_warns_incompatible_time_literals(self) -> None:
        sql_context_value = SqlGenerationContext(
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
            sql_context=sql_context_value,
        )

        self.assertEqual([], result.errors)
        self.assertTrue(result.warnings)
        self.assertIn("incompatible time literals", result.warnings[0])

    def test_sql_validator_warns_month_filter_collapsed_to_single_day(self) -> None:
        sql_context_value = SqlGenerationContext(
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
            sql_context=sql_context_value,
        )

        self.assertEqual([], result.errors)
        self.assertTrue(result.warnings)
        self.assertIn("collapses biz_month filter to a single day", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
