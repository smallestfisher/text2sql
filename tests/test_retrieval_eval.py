from __future__ import annotations

import json
from pathlib import Path
import unittest

from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.semantic_runtime import SemanticRuntime


REPO_ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_CASES_PATH = REPO_ROOT / "eval" / "retrieval_cases.json"


class RetrievalEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.semantic_runtime = SemanticRuntime(domain_config)
        cls.retrieval_service = RetrievalService(
            domain_config=domain_config,
            semantic_runtime=cls.semantic_runtime,
        )
        cls.prompt_builder = PromptBuilder(semantic_runtime=cls.semantic_runtime)
        cls.vector_enabled = cls.retrieval_service.vector_retriever.enabled

    def test_retrieval_cases_cover_expected_prompt_evidence(self) -> None:
        cases = json.loads(RETRIEVAL_CASES_PATH.read_text(encoding="utf-8"))
        self.assertTrue(cases)

        failures: list[str] = []
        for case in cases:
            question = str(case["question"])
            retrieval = self.retrieval_service.retrieve_text(
                question=question,
                semantic_brief=question,
            )
            sql_context = SqlGenerationContext(
                question_type="new",
                subject_domain=case["subject_domain"],
                tables=[],
                semantic_brief=question,
            )
            prompt = self.prompt_builder.build_sql_prompt(
                sql_context,
                retrieval=retrieval,
                question=question,
            )
            summary = prompt["context_summary"]
            available_tables = set(prompt["available_tables"])
            knowledge_ids = set(summary["business_knowledge_entry_ids"])
            join_pattern_ids = set(summary["join_pattern_ids"])

            failures.extend(
                self._missing_items(
                    case,
                    "expected_available_tables",
                    available_tables,
                )
            )
            failures.extend(
                self._missing_items(
                    case,
                    "expected_business_knowledge_ids",
                    knowledge_ids,
                )
            )
            failures.extend(
                self._missing_items(
                    case,
                    "expected_join_pattern_ids",
                    join_pattern_ids,
                )
            )

            # Evidence that only surfaces once the vector channel participates
            # in ranking. The keyword-only path cannot rank this join pattern
            # high enough, so it is validated only when vector retrieval is on.
            if self.vector_enabled:
                failures.extend(
                    self._missing_items(
                        case,
                        "vector_only_expected_join_pattern_ids",
                        join_pattern_ids,
                    )
                )

        self.assertEqual(failures, [])

    def _missing_items(self, case: dict, key: str, actual_values: set[str]) -> list[str]:
        missing = [item for item in case.get(key, []) if item not in actual_values]
        if not missing:
            return []
        return [f"{case['id']} missing {key}: {missing}; actual={sorted(actual_values)}"]


if __name__ == "__main__":
    unittest.main()
