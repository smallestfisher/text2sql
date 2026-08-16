from __future__ import annotations

import inspect
import unittest

from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.models.semantic_types import FilterItem, SortItem, VersionContext
from backend.app.services.sql_validator import SqlValidator
from tests.fixture_metadata import fixture_domain_config


class SqlSafetyValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.domain_config = fixture_domain_config()
        cls.sql_validator = SqlValidator()

    def test_dangerous_keyword_inside_literal_is_not_rejected(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory WHERE GRADE = 'drop shipment' FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
        )

        self.assertEqual([], result.errors)

    def test_validator_api_does_not_expose_dead_permission_filter_parameter(self) -> None:
        self.assertNotIn(
            "required_filter_fields",
            inspect.signature(SqlValidator.validate_detailed).parameters,
        )

    def test_multiple_statements_are_rejected(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory FETCH FIRST 10 ROWS ONLY; SELECT product_ID FROM daily_inventory",
            self.domain_config,
        )

        self.assertIn("multiple SQL statements are not allowed", result.errors)

    def test_non_select_statement_is_rejected(self) -> None:
        result = self.sql_validator.validate_detailed(
            "DELETE FROM inventory_snapshot WHERE model_name = 'A'",
            self.domain_config,
        )

        self.assertIn("only SELECT statements are allowed", result.errors)
        self.assertIn("forbidden keyword detected:delete", result.errors)

    def test_parse_warning_does_not_make_query_invalid(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT SUBSTRING(work_date, 1, 6) AS biz_month FROM production_actuals FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
        )

        self.assertEqual([], result.errors)

    def test_oracle_fetch_first_limit_is_recognized(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
        )

        self.assertEqual([], result.errors)
        self.assertFalse(any("does not include FETCH FIRST" in item for item in result.warnings))
        self.assertNotIn("result_size_risk", result.risk_flags)

    def test_mysql_limit_is_rejected_for_business_sql(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory LIMIT 10",
            self.domain_config,
        )

        self.assertIn("sql uses unsupported MySQL-only syntax: LIMIT", result.errors)

    def test_sql_without_business_source_is_rejected(self) -> None:
        sql_context = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            tables=["daily_inventory"],
        )

        result = self.sql_validator.validate_detailed(
            "SELECT 1 FETCH FIRST 1 ROW ONLY",
            self.domain_config,
            sql_context=sql_context,
        )

        self.assertIn("sql does not reference any physical business source", result.errors)

    def test_sql_parse_error_is_rejected_before_execution(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory WHERE product_ID = FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
        )

        self.assertTrue(any(error.startswith("sql parse error:") for error in result.errors))

    def test_quality_warning_for_unprotected_division(self) -> None:
        result = self.sql_validator.validate_detailed(
            """
            SELECT factory, SUM(actual_qty) / SUM(target_qty) AS achievement_rate
            FROM production_actuals
            GROUP BY factory
            FETCH FIRST 20 ROWS ONLY
            """,
            self.domain_config,
        )

        self.assertEqual([], result.errors)
        self.assertTrue(
            any("division expression should guard zero denominators" in item for item in result.warnings)
        )
        self.assertIn("quality_risk", result.risk_flags)

    def test_quality_warning_for_multi_source_aggregate_without_cte(self) -> None:
        result = self.sql_validator.validate_detailed(
            """
            SELECT a.factory_code, SUM(a.target_IN_glass_qty) AS approved_qty, SUM(b.GLS_qty) AS actual_qty
            FROM monthly_plan_approved a
            JOIN production_actuals b
              ON a.factory_code = b.FACTORY
            GROUP BY a.factory_code
            FETCH FIRST 20 ROWS ONLY
            """,
            self.domain_config,
        )

        self.assertEqual([], result.errors)
        self.assertTrue(
            any("multi-source aggregate query should use CTEs" in item for item in result.warnings)
        )

    def test_quality_warning_for_positional_order_by(self) -> None:
        result = self.sql_validator.validate_detailed(
            """
            SELECT factory, SUM(GLS_qty) AS actual_qty
            FROM production_actuals
            GROUP BY factory
            ORDER BY 2 DESC
            FETCH FIRST 20 ROWS ONLY
            """,
            self.domain_config,
        )

        self.assertEqual([], result.errors)
        self.assertTrue(any("avoid positional ORDER BY" in item for item in result.warnings))

    def test_sources_outside_sql_context_are_rejected(self) -> None:
        sql_context = SqlGenerationContext(
            question_type="new",
            subject_domain="unknown",
            tables=["production_actuals"],
        )

        result = self.sql_validator.validate_detailed(
            "SELECT FGCODE, SUM(sales_qty) AS sales_qty FROM sales_financial_perf GROUP BY FGCODE FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
            sql_context=sql_context,
        )

        self.assertIn("sql references sources outside sql context: sales_financial_perf", result.errors)
        self.assertIn("context_mismatch_risk", result.risk_flags)

    def test_explicit_structured_constraints_are_validation_errors(self) -> None:
        sql_context = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            tables=["daily_inventory"],
            dimensions=["product_ID"],
            filters=[FilterItem(field="GRADE", op="=", value="A")],
            sort=[SortItem(field="product_ID", order="asc")],
            version_context=VersionContext(field="version_code", value="latest"),
            limit=10,
        )

        result = self.sql_validator.validate_detailed(
            "SELECT SUM(panel_qty) AS total_qty FROM daily_inventory FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
            sql_context=sql_context,
        )

        self.assertTrue(any("does not cover all sql context filters" in item for item in result.errors))
        self.assertTrue(any("does not group by required dimensions" in item for item in result.errors))
        self.assertTrue(any("does not preserve sql context sort fields" in item for item in result.errors))
        self.assertTrue(any("missing required version filter" in item for item in result.errors))
        self.assertTrue(any("does not project required dimensions" in item for item in result.errors))

    def test_missing_result_limit_is_a_validation_error(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory",
            self.domain_config,
        )

        self.assertTrue(any("sql does not include FETCH FIRST" in item for item in result.errors))


if __name__ == "__main__":
    unittest.main()
