from __future__ import annotations

from types import SimpleNamespace
import unittest

from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.sql_dialect import SqlDialect


class SqlDialectTests(unittest.TestCase):
    def test_dialect_is_inferred_from_url(self) -> None:
        self.assertEqual(
            SqlDialect.from_name_or_url("oracle+oracledb://u:p@host:1521/?service_name=ORCL").name,
            "oracle",
        )
        self.assertEqual(
            SqlDialect.from_name_or_url("mysql+pymysql://u:p@host/db").name,
            "mysql",
        )

    def test_oracle_result_limit_is_detected(self) -> None:
        dialect = SqlDialect.from_name_or_url("oracle")

        self.assertTrue(dialect.has_result_limit("SELECT * FROM t FETCH FIRST 20 ROWS ONLY"))
        self.assertEqual(
            dialect.extract_result_limit_value("SELECT * FROM t FETCH FIRST 20 ROWS ONLY"),
            20,
        )

    def test_oracle_runtime_limit_sql_is_adapted(self) -> None:
        connector = DatabaseConnector(sql_dialect="oracle")

        self.assertEqual(
            connector._adapt_sql_for_dialect(
                "SELECT * FROM query_logs ORDER BY created_at DESC LIMIT :limit",
                {"limit": 50},
            ),
            "SELECT * FROM query_logs ORDER BY created_at DESC FETCH FIRST 50 ROWS ONLY",
        )

    def test_oracle_prompt_constraints_do_not_request_mysql_limit(self) -> None:
        builder = PromptBuilder(
            semantic_runtime=SimpleNamespace(
                domain_config={
                    "prompt_assets": {
                        "sql_generation": {
                            "base_constraints": [
                                "优先基于真实物理表生成 MySQL 只读 SQL。",
                                "必须包含 LIMIT。",
                            ]
                        }
                    }
                }
            ),
            sql_dialect="oracle",
        )

        constraints = builder._sql_generation_constraints()

        self.assertIn("优先基于真实物理表生成 Oracle SQL 只读查询。", constraints)
        self.assertIn("必须包含结果行数限制，并使用 FETCH FIRST n ROWS ONLY 语法。", constraints)
        self.assertFalse(any(item == "必须包含 LIMIT。" for item in constraints))


if __name__ == "__main__":
    unittest.main()
