from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.app.core.settings import (
    EDITABLE_SPECS,
    FIELD_SPECS,
    SPEC_BY_ENV,
    Settings,
)


class SettingsOverrideTests(unittest.TestCase):
    """Settings.build merges the env baseline with app_config overrides. These
    tests run without a DB — they exercise the pure merge / coercion logic that
    the runtime container and the config API both depend on."""

    def test_defaults_when_no_env_no_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.build()
        self.assertEqual(settings.llm_model, "Qwen/Qwen3-14B")
        self.assertEqual(settings.vector_dimensions, 1024)
        self.assertEqual(settings.vector_top_k, 8)
        self.assertIsNone(settings.llm_cache_prompt)

    def test_override_wins_over_env(self):
        with mock.patch.dict(os.environ, {"LLM_MODEL": "env/model"}, clear=True):
            settings = Settings.build({"LLM_MODEL": "override/model"})
        self.assertEqual(settings.llm_model, "override/model")

    def test_blank_override_reverts_to_env(self):
        with mock.patch.dict(os.environ, {"LLM_MODEL": "env/model"}, clear=True):
            settings = Settings.build({"LLM_MODEL": ""})
        self.assertEqual(settings.llm_model, "env/model")

    def test_blank_override_and_no_env_reverts_to_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.build({"LLM_MODEL": "  "})
        self.assertEqual(settings.llm_model, "Qwen/Qwen3-14B")

    def test_int_coercion(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.build({"VECTOR_TOP_K": "15"})
        self.assertEqual(settings.vector_top_k, 15)
        self.assertIsInstance(settings.vector_top_k, int)

    def test_bad_int_override_is_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                Settings.build({"VECTOR_TOP_K": "not-a-number"})
        self.assertIn("VECTOR_TOP_K", str(ctx.exception))

    def test_bool_coercion(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(Settings.build({"ENABLE_VECTOR_RETRIEVAL": "true"}).enable_vector_retrieval)
            self.assertFalse(Settings.build({"ENABLE_VECTOR_RETRIEVAL": "off"}).enable_vector_retrieval)

    def test_optional_bool_blank_is_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(Settings.build({"LLM_CACHE_PROMPT": ""}).llm_cache_prompt)
            self.assertTrue(Settings.build({"LLM_CACHE_PROMPT": "yes"}).llm_cache_prompt)

    def test_bootstrap_field_ignores_override(self):
        # Non-editable bootstrap fields must never be overridden from the DB.
        with mock.patch.dict(os.environ, {"RUNTIME_DATABASE_URL": "mysql://real"}, clear=True):
            settings = Settings.build({"RUNTIME_DATABASE_URL": "mysql://injected"})
        self.assertEqual(settings.runtime_database_url, "mysql://real")

    def test_auth_secret_is_bootstrap_only(self):
        spec = SPEC_BY_ENV["AUTH_TOKEN_SECRET"]
        self.assertFalse(spec.editable)
        with mock.patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "env-secret"}, clear=True):
            settings = Settings.build({"AUTH_TOKEN_SECRET": "hacked"})
        self.assertEqual(settings.auth_token_secret, "env-secret")

    def test_log_level_uppercased(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "debug"}, clear=True):
            self.assertEqual(Settings.build().log_level, "DEBUG")

    def test_vector_api_base_defaults_for_siliconflow(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.build({"VECTOR_RETRIEVAL_PROVIDER": "siliconflow"})
        self.assertEqual(settings.vector_api_base, "https://api.siliconflow.cn/v1")

    def test_vector_api_base_not_forced_for_other_providers(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.build({"VECTOR_RETRIEVAL_PROVIDER": "openai"})
        self.assertIsNone(settings.vector_api_base)

    def test_editable_set_excludes_bootstrap_fields(self):
        editable_envs = {spec.env for spec in EDITABLE_SPECS}
        self.assertNotIn("RUNTIME_DATABASE_URL", editable_envs)
        self.assertNotIn("AUTH_TOKEN_SECRET", editable_envs)
        self.assertNotIn("APP_NAME", editable_envs)
        self.assertIn("BUSINESS_DATABASE_URL", editable_envs)
        self.assertIn("LLM_MODEL", editable_envs)

    def test_every_spec_attr_exists_on_model(self):
        settings = Settings.build()
        for spec in FIELD_SPECS:
            self.assertTrue(hasattr(settings, spec.attr), f"missing attr {spec.attr}")


if __name__ == "__main__":
    unittest.main()
