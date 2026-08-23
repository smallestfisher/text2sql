from __future__ import annotations

from types import SimpleNamespace
import unittest

from backend.app.api.routes.semantic import domain_summary


class SemanticRouteTests(unittest.TestCase):
    def test_domain_summary_reads_domains_from_semantic_runtime(self) -> None:
        release = SimpleNamespace(version=3)
        runtime = SimpleNamespace(
            release=release,
            semantic_runtime=SimpleNamespace(subject_domains=lambda: ["计划实际"]),
            metadata_registry=SimpleNamespace(
                examples_template=[
                    {
                        "question": "查询计划",
                        "metrics": ["投入量", "投入量"],
                    },
                    {"question": ""},
                ],
                tables_metadata={"daily_plan": {}, "production_actuals": {}},
            ),
        )
        container = SimpleNamespace(
            release_runtime_manager=SimpleNamespace(
                active_release_id=lambda: "rel_test",
                get=lambda _release_id: runtime,
            )
        )

        result = domain_summary(container)

        self.assertEqual(result["version"], "v3")
        self.assertEqual(result["domains"], ["计划实际"])
        self.assertEqual(result["metrics"], ["投入量"])
        self.assertEqual(result["tables"], ["daily_plan", "production_actuals"])
        self.assertEqual(result["starter_questions"], ["查询计划"])
        self.assertTrue(result["published"])


if __name__ == "__main__":
    unittest.main()
