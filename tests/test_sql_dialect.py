from __future__ import annotations

from types import SimpleNamespace
import unittest

from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.sql_dialect import SqlDialect


class SqlDialectTests(unittest.TestCase):
    def test_dialect_accepts_only_explicit_role_names(self) -> None:
        self.assertEqual(SqlDialect.from_name("oracle").name, "oracle")
        self.assertEqual(SqlDialect.from_name("mysql").name, "mysql")

        with self.assertRaisesRegex(ValueError, "unsupported sql dialect"):
            SqlDialect.from_name("oracle+oracledb://u:p@host:1521/?service_name=ORCL")

    def test_oracle_result_limit_is_detected(self) -> None:
        dialect = SqlDialect.from_name("oracle")

        self.assertTrue(dialect.has_result_limit("SELECT * FROM t FETCH FIRST 20 ROWS ONLY"))
        self.assertEqual(
            dialect.extract_result_limit_value("SELECT * FROM t FETCH FIRST 20 ROWS ONLY"),
            20,
        )
        self.assertFalse(dialect.has_result_limit("SELECT * FROM t LIMIT 20"))
        self.assertIsNone(dialect.extract_result_limit_value("SELECT * FROM t LIMIT 20"))

    def test_mysql_result_limit_does_not_accept_oracle_fetch(self) -> None:
        dialect = SqlDialect.from_name("mysql")

        self.assertTrue(dialect.has_result_limit("SELECT * FROM t LIMIT 20"))
        self.assertEqual(dialect.extract_result_limit_value("SELECT * FROM t LIMIT 20"), 20)
        self.assertFalse(dialect.has_result_limit("SELECT * FROM t FETCH FIRST 20 ROWS ONLY"))
        self.assertIsNone(dialect.extract_result_limit_value("SELECT * FROM t FETCH FIRST 20 ROWS ONLY"))

    def test_connector_strips_statement_terminator_without_sql_rewrite(self) -> None:
        connector = DatabaseConnector(sql_dialect="mysql")

        self.assertEqual(
            connector._prepare_sql("SELECT * FROM query_logs ORDER BY created_at DESC LIMIT :limit;"),
            "SELECT * FROM query_logs ORDER BY created_at DESC LIMIT :limit",
        )

    def test_oracle_prompt_constraints_do_not_request_mysql_limit(self) -> None:
        builder = PromptBuilder(
            semantic_runtime=SimpleNamespace(
                domain_config={}
            ),
        )

        constraints = builder._sql_generation_constraints()

        self.assertIn("必须使用 Oracle 语法，并包含 FETCH FIRST n ROWS ONLY 结果限制。", constraints)
        self.assertFalse(any(item == "必须包含 LIMIT。" for item in constraints))


if __name__ == "__main__":
    unittest.main()
