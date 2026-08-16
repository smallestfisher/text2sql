from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.models.semantic_types import FilterItem, TimeContext
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_validator import SqlValidator


def sql_context(sql_context_value: SqlGenerationContext) -> SqlGenerationContext:
    return SqlGenerationContext(**sql_context_value.model_dump(mode="python"))


class TimeFieldFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_dir = Path(__file__).resolve().parent / "fixtures"
        cls.metadata_registry = MetadataRegistry(
            paths={
                "business_knowledge": fixture_dir / "business_knowledge.json",
                "examples_template": fixture_dir / "nl2sql_examples.template.json",
                "tables_metadata": fixture_dir / "tables.json",
                "join_patterns": fixture_dir / "join_patterns.json",
            }
        )
        cls.domain_config = DomainConfigLoader(
            tables_metadata_provider=lambda: cls.metadata_registry.tables_metadata,
        ).load()
        cls.semantic_runtime = SemanticRuntime(
            cls.domain_config,
            metadata_registry=cls.metadata_registry,
        )
        cls.prompt_builder = PromptBuilder(
            semantic_runtime=cls.semantic_runtime,
            metadata_registry=cls.metadata_registry,
        )
        cls.sql_validator = SqlValidator(semantic_runtime=cls.semantic_runtime)

    def test_semantic_runtime_exposes_time_field_metadata(self) -> None:
        metadata = self.semantic_runtime.table_time_field("production_actuals", "work_date")

        self.assertEqual(
            metadata,
            {
                "grain": "day",
                "format": "YYYYMMDD",
                "semantic_names": ["biz_date", "biz_month"],
            },
        )
        self.assertTrue(self.semantic_runtime.is_formatted_string_time_field(metadata))

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

        self.assertIn("month", prompt["evidence_context"]["time_resolution"])
        projections = [
            item["projection_example"]
            for item in prompt["evidence_context"]["time_resolution"]["month"]["candidates"]
        ]
        self.assertTrue(any(item.endswith(" AS biz_month") for item in projections))
        self.assertNotIn("output_shape", prompt["evidence_context"])

    def test_sql_prompt_warns_against_to_char_on_formatted_string_time_fields(self) -> None:
        constraints = self.prompt_builder._sql_generation_constraints()

        self.assertTrue(
            any("字符串格式日期字段" in item and "TO_CHAR" in item for item in constraints),
        )

    def test_sql_validator_rejects_incompatible_time_literals(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            filters=[FilterItem(field="biz_date", op="=", value="2026-04-01")],
            limit=10,
        )

        result = self.sql_validator.validate_detailed(
            "SELECT SUM(GLS_qty) FROM production_actuals WHERE work_date = '2026-04-01' FETCH FIRST 10 ROWS ONLY;",
            self.domain_config,
            sql_context=sql_context_value,
        )

        self.assertTrue(
            any("incompatible time literals" in error for error in result.errors)
        )

    def test_sql_validator_rejects_month_filter_collapsed_to_single_day(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
            time_context=TimeContext(grain="month"),
            limit=10,
        )

        result = self.sql_validator.validate_detailed(
            "SELECT SUM(GLS_qty) FROM production_actuals WHERE work_date = '20260401' FETCH FIRST 10 ROWS ONLY;",
            self.domain_config,
            sql_context=sql_context_value,
        )

        self.assertTrue(
            any("collapses a month-grain time filter to a single day" in error for error in result.errors)
        )

    def test_sql_validator_rejects_to_char_month_on_formatted_string_date(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_input_qty"],
            tables=["monthly_plan_approved", "production_actuals"],
            dimensions=["biz_month", "factory"],
            filters=[
                FilterItem(field="biz_month", op="in", value=["202603", "202604"]),
                FilterItem(field="factory", op="=", value="ARRAY"),
            ],
            limit=200,
        )

        result = self.sql_validator.validate_detailed(
            """
            WITH actual_input AS (
              SELECT TO_CHAR(work_date,'YYYYMM') AS biz_month,
                     FACTORY AS factory,
                     SUM(GLS_qty) AS actual_input_qty
              FROM production_actuals
              WHERE TO_CHAR(work_date,'YYYYMM') IN ('202603','202604')
                AND FACTORY = 'ARRAY'
                AND act_type = '投入'
              GROUP BY TO_CHAR(work_date,'YYYYMM'), FACTORY
            )
            SELECT biz_month, factory, actual_input_qty
            FROM actual_input
            FETCH FIRST 200 ROWS ONLY
            """,
            self.domain_config,
            sql_context=sql_context_value,
        )

        self.assertTrue(result.errors)
        to_char_errors = [error for error in result.errors if "TO_CHAR" in error]
        self.assertTrue(to_char_errors)
        self.assertIn("work_date", to_char_errors[0])

    def test_release_defined_time_aliases_work_without_builtin_business_names(self) -> None:
        metadata_registry = MetadataRegistry(
            documents={
                "examples_template": [],
                "tables_metadata": {
                    "events": {
                        "columns": ["event_id", "occurred_on", "amount"],
                        "time_fields": {
                            "occurred_on": {
                                "grain": "day",
                                "format": "YYYY-MM-DD",
                                "semantic_names": ["business_day", "reporting_period"],
                            }
                        },
                    }
                },
                "business_knowledge": {"entries": []},
                "join_patterns": {"patterns": []},
            }
        )
        domain_config = DomainConfigLoader(
            tables_metadata_provider=lambda: metadata_registry.tables_metadata,
        ).load()
        semantic_runtime = SemanticRuntime(
            domain_config,
            metadata_registry=metadata_registry,
        )
        prompt_builder = PromptBuilder(
            semantic_runtime=semantic_runtime,
            metadata_registry=metadata_registry,
        )
        sql_validator = SqlValidator(semantic_runtime=semantic_runtime)
        context = SqlGenerationContext(
            question_type="new",
            subject_domain="operations",
            tables=["events"],
            metrics=["amount"],
            dimensions=["reporting_period"],
            filters=[FilterItem(field="reporting_period", op="=", value="202604")],
            time_context=TimeContext(grain="month"),
            limit=10,
        )

        prompt = prompt_builder.build_sql_prompt(context, question="April amount")
        month_candidates = prompt["evidence_context"]["time_resolution"]["month"]["candidates"]
        self.assertEqual(month_candidates[0]["field"], "events.occurred_on")
        self.assertTrue(month_candidates[0]["projection_example"].endswith(" AS reporting_period"))

        result = sql_validator.validate_detailed(
            "SELECT SUM(amount) FROM events WHERE occurred_on = '2026-04-01' FETCH FIRST 10 ROWS ONLY",
            domain_config,
            sql_context=context,
        )
        self.assertTrue(
            any("collapses a month-grain time filter to a single day" in error for error in result.errors)
        )


if __name__ == "__main__":
    unittest.main()
