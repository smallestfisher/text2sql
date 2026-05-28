from __future__ import annotations

import unittest

from backend.app.models.classification import QueryIntent
from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.models.session_state import SessionState
from backend.app.models.api import ExecutionResponse
from backend.app.services.intent_service import IntentService
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.question_classifier import QuestionClassifier
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime


class CountingLLMClient:
    def __init__(self) -> None:
        self.intent_calls = 0
        self.classification_calls = 0

    def generate_intent(self, prompt_payload, cancellation_token=None):
        self.intent_calls += 1
        return {
            "subject_domain": "inventory",
            "metrics": ["inventory_qty"],
            "confidence": 0.8,
        }

    def generate_classification_hint(self, prompt_payload, cancellation_token=None):
        self.classification_calls += 1
        return {
            "question_type": "follow_up",
            "subject_domain": "inventory",
            "inherit_context": True,
            "confidence": 0.8,
            "context_delta": {"replace_metrics": ["inventory_qty"]},
        }

    def check_question_relevance(self, prompt_payload, cancellation_token=None):
        return None


class PromptCompactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.prompt_builder = PromptBuilder(semantic_runtime=SemanticRuntime(domain_config))

    def test_classification_prompt_omits_full_intent_and_session_payloads(self) -> None:
        query_intent = QueryIntent(
            normalized_question="继续按客户拆分",
            matched_metrics=["inventory_qty"],
            requested_dimensions=["customer"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
            subject_domain="inventory",
            has_follow_up_cue=True,
        )
        session_state = SessionState(
            session_id="sess_1",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )

        prompt = self.prompt_builder.build_classification_prompt(
            question="继续按客户拆分",
            query_intent=query_intent,
            session_state=session_state,
            semantic_diff={"only_updates_filters": False, "can_execute_without_context": False},
            base_classification={"question_type": "follow_up", "subject_domain": "inventory"},
            allowed_question_types=["follow_up", "new_related", "clarification_needed"],
            candidate_scores={"follow_up": 0.82, "new_related": 0.31},
            arbitration_context={"needs_arbitration": False, "baseline_classification": {}},
        )

        self.assertNotIn("query_intent", prompt)
        self.assertNotIn("session_state", prompt)
        self.assertNotIn("session_semantic_diff", prompt)
        self.assertIn("classification_evidence", prompt)
        self.assertNotIn("baseline_classification", prompt["arbitration_context"])
        self.assertLessEqual(len(prompt["instructions"]["context_delta_examples"]), 2)

    def test_classification_prompt_drops_follow_up_assets_when_not_allowed(self) -> None:
        query_intent = QueryIntent(
            normalized_question="查询2026年4月库存",
            matched_metrics=["inventory_qty"],
            subject_domain="inventory",
        )

        prompt = self.prompt_builder.build_classification_prompt(
            question="查询2026年4月库存",
            query_intent=query_intent,
            session_state=SessionState(session_id="sess_1", subject_domain="demand"),
            semantic_diff={"domain_changed": True},
            base_classification={"question_type": "new_unrelated", "subject_domain": "inventory"},
            allowed_question_types=["new_unrelated", "clarification_needed"],
        )

        self.assertNotIn("context_delta_field_guide", prompt["instructions"])
        self.assertNotIn("context_delta_rules", prompt["instructions"])
        self.assertNotIn("context_delta_examples", prompt["instructions"])
        self.assertNotIn("business_few_shots", prompt["instructions"])

    def test_intent_prompt_uses_compact_signals_and_reduced_output_fields(self) -> None:
        query_intent = QueryIntent(
            normalized_question="最新P版，2026年5月Oxide产品数量是多少",
            matched_metrics=["product_count"],
            requested_dimensions=["biz_month"],
            filters=[FilterItem(field="PM_VERSION", op="latest_n", value={"count": 1, "source_table": "p_demand"})],
            subject_domain="demand",
            has_follow_up_cue=False,
            has_explicit_slots=True,
        )
        session_state = SessionState(
            session_id="sess_2",
            subject_domain="demand",
            metrics=["product_count"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="source_table", op="=", value="p_demand")],
            last_question_type="new",
        )

        prompt = self.prompt_builder.build_intent_prompt(
            question="最新P版，2026年5月Oxide产品数量是多少",
            query_intent=query_intent,
            session_state=session_state,
        )

        self.assertIn("shallow_signals", prompt)
        self.assertIn("session_focus", prompt)
        self.assertNotIn("shallow_parse", prompt)
        self.assertNotIn("session_state", prompt)
        self.assertNotIn("business_few_shots", prompt["instructions"])
        self.assertNotIn("question_type", prompt["instructions"]["fields"])
        self.assertNotIn("inherit_context", prompt["instructions"]["fields"])
        self.assertLessEqual(len(prompt["domain_hints"]["domain_fields"]), 41)
        self.assertIn("product_ID", prompt["domain_hints"]["domain_fields"])
        self.assertIn("PM_VERSION", prompt["domain_hints"]["domain_fields"])

    def test_intent_prompt_hides_supported_domains_when_domain_is_known(self) -> None:
        query_intent = QueryIntent(
            normalized_question="查询2026年4月库存",
            matched_metrics=["inventory_qty"],
            subject_domain="inventory",
        )

        prompt = self.prompt_builder.build_intent_prompt(
            question="查询2026年4月库存",
            query_intent=query_intent,
            session_state=None,
        )

        self.assertEqual(prompt["domain_hints"]["supported_domains"], [])

    def test_prompt_assets_are_aligned_with_compact_contracts(self) -> None:
        assets = self.prompt_builder._prompt_assets()

        classification_constraints = "\n".join(assets["classification"]["constraints"])
        intent_constraints = "\n".join(assets["intent_understanding"]["constraints"])

        self.assertNotIn("query_intent", classification_constraints)
        self.assertNotIn("session_semantic_diff", classification_constraints)
        self.assertIn("classification_evidence", classification_constraints)
        self.assertNotIn("shallow_parse", intent_constraints)
        self.assertIn("shallow_signals", intent_constraints)
        self.assertNotIn("question_type", assets["intent_understanding"]["fields"])
        self.assertNotIn("inherit_context", assets["intent_understanding"]["fields"])
    def test_intent_service_skips_llm_for_clear_parser_intent_without_session(self) -> None:
        llm_client = CountingLLMClient()
        service = IntentService(llm_client, self.prompt_builder)
        query_intent = QueryIntent(
            normalized_question="查询2026年4月库存",
            matched_metrics=["inventory_qty"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
            subject_domain="inventory",
            has_explicit_slots=True,
        )

        result = service.generate_intent(
            question="查询2026年4月库存",
            query_intent=query_intent,
            session_state=None,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["raw"]["mode"], "parser_shortcut")
        self.assertEqual(result["intent"].source, "parser")
        self.assertEqual(llm_client.intent_calls, 0)

    def test_classifier_skips_llm_for_high_confidence_baseline(self) -> None:
        llm_client = CountingLLMClient()
        classifier = QuestionClassifier(
            semantic_runtime=self.prompt_builder.semantic_runtime,
            llm_client=llm_client,
            prompt_builder=self.prompt_builder,
        )
        query_intent = QueryIntent(
            normalized_question="查询2026年4月库存",
            matched_metrics=["inventory_qty"],
            filters=[FilterItem(field="biz_month", op="=", value="202604")],
            subject_domain="inventory",
            has_explicit_slots=True,
        )
        session_state = SessionState(
            session_id="sess_1",
            subject_domain="demand",
            metrics=["demand_qty"],
        )

        classification, warnings = classifier.classify(
            question="查询2026年4月库存",
            query_intent=query_intent,
            session_state=session_state,
        )

        self.assertEqual(classification.question_type, "new_unrelated")
        self.assertEqual(classification.subject_domain, "inventory")
        self.assertEqual(classifier.last_debug_info()["decision_source"], "baseline_high_confidence")
        self.assertEqual(classifier.last_debug_info()["llm_skipped_reason"], "baseline_high_confidence")
        self.assertEqual(llm_client.classification_calls, 0)
        self.assertEqual(warnings, [])

    def test_orchestrator_validation_repair_gate_only_allows_repairable_errors(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=[],
            sql="SELECT 1",
            llm_sql="SELECT 1",
            plan_errors=[],
            sql_prompt={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "no_validation_errors")

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=["sql is empty"],
            sql=None,
            llm_sql=None,
            plan_errors=[],
            sql_prompt={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sql_missing")

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=["sql references unknown sources: fake_table"],
            sql="SELECT * FROM fake_table",
            llm_sql="SELECT * FROM fake_table",
            plan_errors=[],
            sql_prompt={},
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "repairable_validation_error")

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=["forbidden keyword detected:delete"],
            sql="DELETE FROM t",
            llm_sql="DELETE FROM t",
            plan_errors=[],
            sql_prompt={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "non_repairable_validation_error")

    def test_orchestrator_execution_repair_gate_skips_governance_and_allows_sql_errors(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.llm_client = CountingLLMClient()
        orchestrator.llm_client.enabled = True
        governance_failure = ExecutionResponse(
            executed=False,
            status="blocked",
            sql="SELECT 1 -- no",
            row_count=0,
            columns=[],
            rows=[],
            errors=["sql comments are not allowed in execution stage"],
            warnings=[],
            error_category="governance",
        )

        allowed, reason = orchestrator._should_repair_execution_failure(
            execution=governance_failure,
            sql="SELECT 1 -- no",
            llm_sql="SELECT 1 -- no",
            sql_prompt={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "non_repairable_execution_category")

        sql_failure = ExecutionResponse(
            executed=False,
            status="db_error",
            sql="SELECT bad_col FROM daily_inventory",
            row_count=0,
            columns=[],
            rows=[],
            errors=["ORA-00904: invalid identifier"],
            warnings=[],
            error_category="database",
        )
        allowed, reason = orchestrator._should_repair_execution_failure(
            execution=sql_failure,
            sql="SELECT bad_col FROM daily_inventory",
            llm_sql="SELECT bad_col FROM daily_inventory",
            sql_prompt={},
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "repairable_execution_error")


    def test_sql_prompt_uses_compact_query_contract_and_relevant_table_schemas(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"})],
            analysis_mode="distribution",
            limit=50,
        )

        prompt = self.prompt_builder.build_sql_prompt(query_plan, question="最新 OMS 库存")

        self.assertIn("query_contract", prompt)
        self.assertIn("table_schemas", prompt)
        self.assertNotIn("query_plan", prompt)
        self.assertNotIn("tables_metadata", prompt)
        self.assertNotIn("allowed_fields", prompt)
        self.assertNotIn("context_delta", prompt["query_contract"])
        self.assertNotIn("reason", prompt["query_contract"])
        self.assertEqual(prompt["query_contract"]["tables"], ["oms_inventory"])
        self.assertEqual(prompt["query_contract"]["limit"], 50)

        schema = prompt["table_schemas"]["oms_inventory"]
        schema_text = "\n".join(schema["columns"])
        self.assertIn("report_month", schema_text)
        self.assertIn("panel_qty", schema_text)
        self.assertIn("glass_qty", schema_text)
        self.assertIn("product_ID", schema_text)
        self.assertNotIn("LGORT_DL", schema_text)
        self.assertIn("time_fields", schema)
        self.assertIn("relationships", schema)
        self.assertIn("table_schemas_count", prompt["context_summary"])
        self.assertNotIn("tables_metadata_count", prompt["context_summary"])
    def test_sql_prompt_preserves_demand_horizontal_and_product_attribute_columns(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="demand",
            metrics=["product_count"],
            tables=["p_demand", "product_attributes"],
            filters=[
                FilterItem(field="PM_VERSION", op="latest_n", value={"count": 1, "source_table": "p_demand"}),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
                FilterItem(field="demand_month", op="=", value="202605"),
            ],
            limit=50,
        )

        prompt = self.prompt_builder.build_sql_prompt(
            query_plan,
            question="最新P版，2026年5月Oxide产品数量是多少",
        )

        demand_schema_text = "\n".join(prompt["table_schemas"]["p_demand"]["columns"])
        attributes_schema_text = "\n".join(prompt["table_schemas"]["product_attributes"]["columns"])

        self.assertIn("REQUIREMENT_QTY", demand_schema_text)
        self.assertIn("NEXT_REQUIREMENT", demand_schema_text)
        self.assertIn("MONTH7", demand_schema_text)
        self.assertIn("IS_OXIDE", attributes_schema_text)
        self.assertIn("Y=是，N=否", attributes_schema_text)
        self.assertIn("IS_xxx 字段使用 Y/N 标记", prompt["business_notes"])

    def test_sql_prompt_compacts_business_notes_and_examples(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[
                FilterItem(field="source_table", op="=", value="oms_inventory"),
                FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"}),
            ],
            analysis_mode="distribution",
        )

        prompt = self.prompt_builder.build_sql_prompt(query_plan, question="最新 OMS 库存库龄分布")
        business_notes = prompt["business_notes"]

        self.assertLessEqual(len(business_notes), 1600)
        self.assertIn("不要自行追加 common_categories", business_notes)
        self.assertIn("默认取 oms_inventory.report_month 的最新月份", business_notes)
        self.assertIn(">12M = SIX_AGE_panel_qty + SEVEN_AGE_panel_qty + EUGHT_AGE_panel_qty", business_notes)
        self.assertNotIn("[join_pattern:", business_notes)

    def test_sql_prompt_retrieved_examples_omit_full_intent_text(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"})],
            analysis_mode="distribution",
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="inventory_oms_latest_ttl_age_distribution_001",
                    score=3.0,
                    summary="OMS age bucket example",
                    matched_features=["metrics:inventory_qty", "filters:biz_month", "extra:a", "extra:b", "extra:c", "extra:d"],
                )
            ]
        )

        prompt = self.prompt_builder.build_sql_prompt(
            query_plan,
            retrieval=retrieval,
            question="最新 OMS 库存库龄分布",
        )
        examples = prompt["instructions"]["few_shot"]["retrieved_examples"]

        self.assertEqual(len(examples), 1)
        self.assertNotIn("intent", examples[0])
        self.assertIn("semantic_shape", examples[0])
        self.assertLessEqual(len(examples[0]["matched_features"]), 5)
        self.assertEqual(examples[0]["semantic_shape"]["subject_domain"], "inventory")


    def test_sql_prompt_assets_reference_compact_contract_names(self) -> None:
        assets = self.prompt_builder._prompt_assets()
        sql_text = "\n".join(
            assets["sql_generation"]["base_preferences"]
            + assets["sql_generation"]["base_constraints"]
            + assets["sql_generation"]["latest_n_preferences"]
        )

        self.assertIn("query_contract", sql_text)
        self.assertIn("table_schemas", sql_text)
        self.assertNotIn("tables_metadata", sql_text)
        self.assertNotIn("query_plan.", sql_text)


if __name__ == "__main__":
    unittest.main()
