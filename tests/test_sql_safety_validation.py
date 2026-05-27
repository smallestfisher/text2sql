from __future__ import annotations

import unittest

from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.sql_validator import SqlValidator


class SqlSafetyValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.domain_config = DomainConfigLoader().load()
        cls.sql_validator = SqlValidator()

    def test_dangerous_keyword_inside_literal_is_not_rejected(self) -> None:
        result = self.sql_validator.validate_detailed(
            "SELECT product_ID FROM daily_inventory WHERE GRADE = 'drop shipment' FETCH FIRST 10 ROWS ONLY",
            self.domain_config,
        )

        self.assertEqual([], result.errors)

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
            "SELECT SUBSTRING(work_date, 1, 6) AS biz_month FROM production_actuals LIMIT 10",
            self.domain_config,
        )

        self.assertEqual([], result.errors)


if __name__ == "__main__":
    unittest.main()
