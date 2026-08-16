from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.app.core.settings import (
    FIELD_SPECS,
    SPEC_BY_ENV,
    Settings,
)


class SettingsOverrideTests(unittest.TestCase):
    """Deployment settings come only from environment variables and defaults."""

    def test_defaults_when_no_env_no_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.llm_model, "Qwen/Qwen3-14B")
        self.assertEqual(settings.vector_dimensions, 1024)
        self.assertEqual(settings.vector_top_k, 8)
        self.assertIsNone(settings.llm_cache_prompt)
        self.assertEqual(settings.runtime_database_url, "sqlite:///./runtime/runtime.db")
        self.assertEqual(settings.vector_cache_dir, "./runtime/vector-cache")

    def test_environment_wins_over_default(self):
        with mock.patch.dict(os.environ, {"LLM_MODEL": "env/model"}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.llm_model, "env/model")

    def test_blank_environment_reverts_to_default(self):
        with mock.patch.dict(os.environ, {"LLM_MODEL": "  "}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.llm_model, "Qwen/Qwen3-14B")

    def test_int_coercion(self):
        with mock.patch.dict(os.environ, {"VECTOR_TOP_K": "15"}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.vector_top_k, 15)
        self.assertIsInstance(settings.vector_top_k, int)

    def test_bad_int_environment_value_is_rejected(self):
        with mock.patch.dict(os.environ, {"VECTOR_TOP_K": "not-a-number"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                Settings.build()
        self.assertIn("VECTOR_TOP_K", str(ctx.exception))

    def test_bool_coercion(self):
        with mock.patch.dict(os.environ, {"ENABLE_VECTOR_RETRIEVAL": "true"}, clear=True):
            self.assertTrue(Settings.build().enable_vector_retrieval)
        with mock.patch.dict(os.environ, {"ENABLE_VECTOR_RETRIEVAL": "off"}, clear=True):
            self.assertFalse(Settings.build().enable_vector_retrieval)

    def test_optional_bool_blank_is_none(self):
        with mock.patch.dict(os.environ, {"LLM_CACHE_PROMPT": ""}, clear=True):
            self.assertIsNone(Settings.build().llm_cache_prompt)
        with mock.patch.dict(os.environ, {"LLM_CACHE_PROMPT": "yes"}, clear=True):
            self.assertTrue(Settings.build().llm_cache_prompt)

    def test_runtime_database_url_comes_from_environment(self):
        with mock.patch.dict(os.environ, {"RUNTIME_DATABASE_URL": "mysql://real"}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.runtime_database_url, "mysql://real")

    def test_auth_secret_is_deployment_only(self):
        spec = SPEC_BY_ENV["AUTH_TOKEN_SECRET"]
        self.assertFalse(spec.editable)
        with mock.patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "env-secret"}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.auth_token_secret, "env-secret")

    def test_business_database_config_is_deployment_only(self):
        with mock.patch.dict(
            os.environ,
            {
                "BUSINESS_DATABASE_URL": "oracle+oracledb://env-db",
                "BUSINESS_DATABASE_SCHEMAS": "ADMIN,REPORTING",
            },
            clear=True,
        ):
            settings = Settings.build()
        self.assertEqual(settings.business_database_url, "oracle+oracledb://env-db")
        self.assertEqual(settings.business_schema_scope(), ["ADMIN", "REPORTING"])

    def test_log_level_uppercased(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "debug"}, clear=True):
            self.assertEqual(Settings.build().log_level, "DEBUG")

    def test_vector_api_base_defaults_for_siliconflow(self):
        with mock.patch.dict(os.environ, {"VECTOR_RETRIEVAL_PROVIDER": "siliconflow"}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.vector_api_base, "https://api.siliconflow.cn/v1")

    def test_vector_api_base_not_forced_for_other_providers(self):
        with mock.patch.dict(os.environ, {"VECTOR_RETRIEVAL_PROVIDER": "openai"}, clear=True):
            settings = Settings.build()
        self.assertIsNone(settings.vector_api_base)

    def test_all_settings_are_read_only_at_runtime(self):
        self.assertTrue(all(not spec.editable for spec in FIELD_SPECS))

    def test_every_spec_attr_exists_on_model(self):
        settings = Settings.build()
        for spec in FIELD_SPECS:
            self.assertTrue(hasattr(settings, spec.attr), f"missing attr {spec.attr}")


if __name__ == "__main__":
    unittest.main()
