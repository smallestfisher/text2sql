from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.app.api.routes.admin import (
    _active_vector_health,
    _dashboard_sections,
    _empty_vector_retrieval_status,
    _runtime_probes,
)


def _raise_boom() -> dict:
    raise RuntimeError("boom")


class AdminDashboardHelperTests(unittest.TestCase):
    def test_active_vector_health_uses_release_scoped_retriever(self) -> None:
        fallback = {"ready": False, "indexed_document_count": 0}
        active = {"ready": True, "indexed_document_count": 11}
        container = SimpleNamespace(
            vector_retriever=SimpleNamespace(health=lambda: fallback),
            release_runtime_manager=SimpleNamespace(
                active_release_id=lambda: "rel_test",
                get=lambda _release_id: SimpleNamespace(
                    retrieval_service=SimpleNamespace(
                        vector_retriever=SimpleNamespace(health=lambda: active)
                    )
                ),
            ),
        )

        self.assertEqual(_active_vector_health(container), active)

    def test_dashboard_sections_keep_success_when_one_section_fails(self) -> None:
        section_errors: dict[str, str] = {}

        with self.assertLogs("backend.app.api.routes.admin", level="WARNING"):
            result = _dashboard_sections(
                section_errors,
                {
                    "ok": (lambda: {"value": 1}, {"value": 0}),
                    "bad": (_raise_boom, {"value": 0}),
                },
            )

        self.assertEqual(result["ok"], {"value": 1})
        self.assertEqual(result["bad"], {"value": 0})
        self.assertIn("RuntimeError: boom", section_errors["bad"])

    def test_runtime_probes_return_error_status_with_probe_fallback(self) -> None:
        with self.assertLogs("backend.app.api.routes.admin", level="WARNING"):
            result = _runtime_probes(
                {
                    "vector_retrieval": (
                        _raise_boom,
                        _empty_vector_retrieval_status(),
                    ),
                }
            )

        self.assertFalse(result["vector_retrieval"]["enabled"])
        self.assertEqual(result["vector_retrieval"]["status"], "error")
        self.assertEqual(result["vector_retrieval"]["error"], "boom")


if __name__ == "__main__":
    unittest.main()
