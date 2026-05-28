from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from backend.app.models.classification import QueryIntent
from backend.app.models.query_plan import FilterItem, QueryPlan, TimeContext, TimeRange, VersionContext
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.models.session_state import SessionState
from backend.app.models.api import ExecutionResponse
from backend.app.repositories.metadata_repository import FileMetadataRepository
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.metadata_service import MetadataService
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.intent_service import IntentService
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.question_classifier import QuestionClassifier
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.example_factory import ExampleFactory
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.semantic_runtime import SemanticRuntime


class EmptyAuditRepository:
    def list_records(self):
        return []


class StaticExampleRegistry:
    def __init__(self, examples: list[dict]) -> None:
        self.examples_template = examples

    @property
    def tables_metadata(self) -> dict:
        return {}

    @property
    def business_knowledge_entries(self) -> list[dict]:
        return []

    @property
    def join_patterns(self) -> list[dict]:
        return []


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


class EmptyIntentNoClassificationLLMClient:
    def __init__(self) -> None:
        self.intent_calls = 0
        self.classification_calls = 0

    def generate_intent(self, prompt_payload, cancellation_token=None):
        self.intent_calls += 1
        return {}

    def generate_classification_hint(self, prompt_payload, cancellation_token=None):
        self.classification_calls += 1
        raise AssertionError("classification LLM should not run for clear detail follow-up")

    def check_question_relevance(self, prompt_payload, cancellation_token=None):
        return None


class ContextEchoDetailLLMClient(EmptyIntentNoClassificationLLMClient):
    def generate_intent(self, prompt_payload, cancellation_token=None):
        self.intent_calls += 1
        return {
            "subject_domain": "demand",
            "metrics": [],
            "entities": ["stage_product"],
            "dimensions": ["FGCODE"],
            "filters": [],
            "time_context": {
                "grain": "month",
                "range": {"start": "2026-05-01", "end": "2026-05-31"},
            },
            "version_context": {"field": "PM_VERSION", "value": "LATEST_N:1"},
            "analysis_mode": "detail",
            "confidence": 0.9,
        }


class PromptCompactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.domain_config = domain_config
        cls.semantic_runtime = SemanticRuntime(domain_config)
        cls.prompt_builder = PromptBuilder(semantic_runtime=cls.semantic_runtime)

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
            matched_metrics=["product_count", "demand_qty"],
            requested_dimensions=["biz_month"],
            filters=[FilterItem(field="PM_VERSION", op="latest_n", value={"count": 1, "source_table": "p_demand"})],
            subject_domain="demand",
            has_follow_up_cue=False,
            has_explicit_slots=True,
        )
        session_state = SessionState(
            session_id="sess_2",
            subject_domain="demand",
            metrics=["product_count", "demand_qty"],
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
            metrics=["product_count", "demand_qty"],
            tables=["p_demand", "product_attributes"],
            filters=[
                FilterItem(field="PM_VERSION", op="latest_n", value={"count": 1, "source_table": "p_demand"}),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
                FilterItem(field="biz_month", op="=", value="202605"),
                FilterItem(field="demand_qty", op=">", value=0),
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
        self.assertIn("demand_qty", prompt["query_contract"]["metrics"])
        self.assertIn("IS_OXIDE", attributes_schema_text)
        self.assertIn("Y=是，N=否", attributes_schema_text)
        self.assertIn("IS_xxx 字段使用 Y/N 标记", prompt["business_notes"])
        filter_resolution = prompt["field_resolution"]["filters"]["biz_month"]
        self.assertIn("p_demand.MONTH", filter_resolution["physical_candidates"])
        self.assertIn("先在 CTE 中把 p_demand/v_demand 横表展开为 demand_month", filter_resolution["filter_examples"][0])
        self.assertNotIn("p_demand.MONTH = '202605'", "\n".join(filter_resolution["filter_examples"]))
        demand_qty_resolution = prompt["field_resolution"]["filters"]["demand_qty"]
        self.assertIn("NVL(demand_qty, 0) > 0", demand_qty_resolution["filter_examples"][0])

    def test_demand_product_count_question_also_requests_total_demand(self) -> None:
        llm_client = EmptyIntentNoClassificationLLMClient()
        planner = QueryPlanner(
            self.domain_config,
            llm_client,
            self.prompt_builder,
            IntentService(llm_client, self.prompt_builder),
            IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )

        query_intent, classification, query_plan, _warnings = planner.create_plan(
            "最新P版，2026年5月Oxide产品数量是多少"
        )

        self.assertEqual(classification.question_type, "new")
        self.assertIn("product_count", query_intent.matched_metrics)
        self.assertIn("demand_qty", query_intent.matched_metrics)
        self.assertIn("product_count", query_plan.metrics)
        self.assertIn("demand_qty", query_plan.metrics)
        self.assertIn(FilterItem(field="demand_qty", op=">", value=0), query_plan.filters)

    def test_detail_follow_up_replaces_product_count_with_product_model_list(self) -> None:
        llm_client = EmptyIntentNoClassificationLLMClient()
        planner = QueryPlanner(
            self.domain_config,
            llm_client,
            self.prompt_builder,
            IntentService(llm_client, self.prompt_builder),
            IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_detail",
            subject_domain="demand",
            tables=["p_demand", "product_attributes"],
            metrics=["product_count"],
            filters=[
                FilterItem(field="source_table", op="=", value="p_demand"),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
                FilterItem(field="biz_month", op="=", value="202605"),
                FilterItem(field="demand_qty", op=">", value=0),
            ],
        )

        trace = planner.build_planning_trace(
            question="给出这些产品的具体型号啊",
            session_state=session_state,
        )
        query_plan = planner.build_plan_from_intent(
            query_intent=trace["query_intent"],
            classification=trace["classification"],
            session_state=session_state,
        )

        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertEqual(llm_client.classification_calls, 0)
        self.assertEqual(query_plan.analysis_mode, "detail")
        self.assertEqual(query_plan.metrics, [])
        self.assertIn("FGCODE", query_plan.dimensions)
        self.assertIn(FilterItem(field="IS_OXIDE", op="=", value="Y"), query_plan.filters)
        self.assertIn(FilterItem(field="biz_month", op="=", value="202605"), query_plan.filters)
        self.assertIn(FilterItem(field="demand_qty", op=">", value=0), query_plan.filters)
        self.assertIn("p_demand", query_plan.tables)
        self.assertIn("product_attributes", query_plan.tables)
        self.assertNotIn("sales_financial_perf", query_plan.tables)

        prompt = self.prompt_builder.build_sql_prompt(
            query_plan,
            question="给出这些产品的具体型号啊",
        )
        self.assertEqual(prompt["query_contract"]["metrics"], [])
        self.assertIn("FGCODE", prompt["shape_contract"]["required_projection"])
        self.assertIn("DISTINCT 去重", "\n".join(prompt["instructions"]["sql_preferences"]))

    def test_detail_follow_up_inherits_context_when_llm_echoes_session_time_version(self) -> None:
        llm_client = ContextEchoDetailLLMClient()
        planner = QueryPlanner(
            self.domain_config,
            llm_client,
            self.prompt_builder,
            IntentService(llm_client, self.prompt_builder),
            IntentNormalizer(self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_detail",
            subject_domain="demand",
            tables=["p_demand", "product_attributes"],
            metrics=["product_count", "demand_qty"],
            filters=[
                FilterItem(field="source_table", op="=", value="p_demand"),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
                FilterItem(field="demand_month", op="=", value="202605"),
                FilterItem(field="PM_VERSION", op="latest_n", value={"count": 1, "source_table": "p_demand"}),
                FilterItem(field="demand_qty", op=">", value=0),
            ],
            time_context=TimeContext(
                grain="month",
                range=TimeRange(start="2026-05-01", end="2026-05-31"),
            ),
            version_context=VersionContext(field="PM_VERSION", value="LATEST_N:1"),
        )

        trace = planner.build_planning_trace(
            question="对应的具体型号是什么",
            session_state=session_state,
        )
        query_plan = planner.build_plan_from_intent(
            query_intent=trace["query_intent"],
            classification=trace["classification"],
            session_state=session_state,
        )

        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertTrue(trace["classification"].inherit_context)
        self.assertFalse(trace["query_intent"].has_follow_up_cue)
        self.assertEqual(llm_client.classification_calls, 0)
        self.assertTrue(trace["semantic_diff"]["context_dependent_detail_request"])
        self.assertEqual(query_plan.metrics, [])
        self.assertEqual(query_plan.tables, ["p_demand", "product_attributes"])
        self.assertIn("FGCODE", query_plan.dimensions)
        self.assertIn(FilterItem(field="source_table", op="=", value="p_demand"), query_plan.filters)
        self.assertIn(FilterItem(field="IS_OXIDE", op="=", value="Y"), query_plan.filters)
        self.assertIn(FilterItem(field="PM_VERSION", op="latest_n", value={"count": 1, "source_table": "p_demand"}), query_plan.filters)
        self.assertIn(FilterItem(field="demand_qty", op=">", value=0), query_plan.filters)
        self.assertNotIn("v_demand", query_plan.tables)

    def test_sql_prompt_includes_physical_time_filter_examples_for_plan_actual_compare(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["approved_input_qty", "actual_input_qty", "input_gap_qty", "input_achievement_rate"],
            tables=["monthly_plan_approved", "production_actuals"],
            filters=[
                FilterItem(field="biz_month", op="between", value=["2026-02-01", "2026-02-28"]),
                FilterItem(field="factory", op="=", value="ARRAY"),
                FilterItem(field="act_type", op="=", value="投入"),
            ],
        )

        prompt = self.prompt_builder.build_sql_prompt(
            query_plan,
            question="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        filter_resolution = prompt["field_resolution"]["filters"]["biz_month"]
        self.assertIn("monthly_plan_approved.plan_month", filter_resolution["physical_candidates"])
        self.assertIn("monthly_plan_approved.plan_month = '202602'", filter_resolution["filter_examples"])
        self.assertIn("SUBSTR(production_actuals.work_date, 1, 6) = '202602'", filter_resolution["filter_examples"])
        self.assertNotIn("'2026-02-01'", "\n".join(filter_resolution["filter_examples"]))

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
        self.assertIn("sql", examples[0])
        self.assertLessEqual(len(examples[0]["matched_features"]), 5)
        self.assertEqual(examples[0]["semantic_shape"]["subject_domain"], "inventory")

    def test_lightweight_example_can_be_normalized_from_question_and_sql(self) -> None:
        factory = ExampleFactory(self.domain_config, self.semantic_runtime)

        example = factory.normalize(
            {
                "question": "2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
                "sql": """
WITH approved_agg AS (
  SELECT factory, SUM(target_IN_panel_qty) AS approved_input_panel_qty
  FROM monthly_plan_approved
  WHERE month = '202602'
  GROUP BY factory
), actual_agg AS (
  SELECT factory, SUM(GLS_qty) AS actual_input_panel_qty
  FROM production_actuals
  WHERE SUBSTR(work_date, 1, 6) = '202602'
    AND process_type = 'ARRAY'
    AND in_out_type = 'IN'
  GROUP BY factory
)
SELECT COALESCE(a.factory, b.factory) AS factory,
       a.approved_input_panel_qty,
       b.actual_input_panel_qty,
       a.approved_input_panel_qty - b.actual_input_panel_qty AS input_panel_gap_qty,
       CASE
         WHEN a.approved_input_panel_qty = 0 THEN NULL
         ELSE b.actual_input_panel_qty / a.approved_input_panel_qty
       END AS input_panel_achievement_rate
FROM approved_agg a
FULL OUTER JOIN actual_agg b ON a.factory = b.factory
FETCH FIRST 200 ROWS ONLY
""".strip(),
                "notes": "审批和实际先各自聚合，再按工厂合并。",
                "tags": ["approved_vs_actual", "aggregate_then_join"],
                "dimensions": ["factory"],
                "metrics": [
                    "approved_input_panel_qty",
                    "actual_input_panel_qty",
                    "input_panel_gap_qty",
                    "input_panel_achievement_rate",
                ],
            }
        )

        self.assertEqual(example.question_type, "new")
        self.assertEqual(example.subject_domain, "plan_actual")
        self.assertEqual(example.tables, ["monthly_plan_approved", "production_actuals"])
        self.assertEqual(example.dimensions, ["factory"])
        self.assertIn("approved_input_panel_qty", example.metrics)
        self.assertIn("aggregate_then_join", example.coverage_tags)
        self.assertEqual(example.result_shape, "factory")

    def test_lightweight_example_can_infer_domain_from_sql_tables(self) -> None:
        factory = ExampleFactory(self.domain_config, self.semantic_runtime)

        example = factory.normalize(
            {
                "question": "看一下这个分布",
                "sql": "SELECT report_month, SUM(panel_qty) AS inventory_qty FROM oms_inventory GROUP BY report_month FETCH FIRST 50 ROWS ONLY",
            }
        )

        self.assertEqual(example.subject_domain, "inventory")
        self.assertEqual(example.tables, ["oms_inventory"])
        self.assertIn("inventory", example.coverage_tags)

    def test_complete_example_shape_is_rejected_as_template_input(self) -> None:
        factory = ExampleFactory(self.domain_config, self.semantic_runtime)

        with self.assertRaisesRegex(Exception, "Extra inputs are not permitted"):
            factory.normalize(
                {
                    "id": "old_complete_example",
                    "question": "最新 OMS 库存",
                    "normalized_question": "最新 oms 库存",
                    "intent": "legacy complete shape",
                    "question_type": "new",
                    "subject_domain": "inventory",
                    "tables": ["oms_inventory"],
                    "filters": [],
                    "join_path": [],
                    "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 1 ROW ONLY",
                }
            )

    def test_metadata_create_example_persists_template_shape_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            examples_path = Path(temp_dir) / "examples.json"
            examples_path.write_text("[]\n", encoding="utf-8")
            registry = MetadataRegistry(
                paths={
                    "examples_template": examples_path,
                    "tables_metadata": Path("semantic/tables.json"),
                    "business_knowledge": Path("semantic/business_knowledge.json"),
                    "join_patterns": Path("semantic/join_patterns.json"),
                    "domain_config": Path("semantic/domain_config.json"),
                    "query_plan_schema": Path("schemas/query_plan.schema.json"),
                    "session_state_schema": Path("schemas/session_state.schema.json"),
                }
            )
            retrieval_service = type(
                "FakeRetrievalService",
                (),
                {
                    "validate_example": lambda _self, payload: ExampleFactory(self.domain_config, self.semantic_runtime).normalize(payload),
                    "dump_example_template": lambda _self, payload: ExampleFactory(self.domain_config, self.semantic_runtime).dump_template(payload),
                    "reload": lambda _self: None,
                },
            )()
            service = MetadataService(
                metadata_repository=FileMetadataRepository(registry),
                domain_config_loader=DomainConfigLoader(),
                audit_repository=EmptyAuditRepository(),
            )

            response = service.create_example(
                {
                    "question": "最新 OMS 库存",
                    "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 1 ROW ONLY",
                    "subject_domain": "inventory",
                    "metrics": ["inventory_qty"],
                    "tags": ["smoke"],
                },
                retrieval_service=retrieval_service,
            )

            stored = registry.read("examples_template")
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["question"], "最新 OMS 库存")
            self.assertEqual(stored[0]["tags"], ["smoke"])
            self.assertEqual(response.template.tags, ["smoke"])
            self.assertNotIn("normalized_question", stored[0])
            self.assertNotIn("question_type", stored[0])
            self.assertNotIn("tables", stored[0])
            self.assertNotIn("filters", stored[0])

    def test_sql_prompt_includes_safe_oracle_example_sql(self) -> None:
        prompt_builder = PromptBuilder(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=StaticExampleRegistry(
                [
                    {
                        "id": "inventory_safe_oracle_example",
                        "question": "最新 OMS 库存库龄分布",
                        "sql": "SELECT report_month, SUM(panel_qty) AS inventory_qty FROM oms_inventory GROUP BY report_month FETCH FIRST 50 ROWS ONLY",
                        "metrics": ["inventory_qty"],
                    }
                ]
            ),
        )
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="inventory_safe_oracle_example",
                    score=3.0,
                    summary="safe oracle example",
                    matched_features=["metrics:inventory_qty"],
                )
            ]
        )

        prompt = prompt_builder.build_sql_prompt(query_plan, retrieval=retrieval, question="最新 OMS 库存")
        example = prompt["instructions"]["few_shot"]["retrieved_examples"][0]

        self.assertIn("FETCH FIRST 50 ROWS ONLY", example["sql"])
        self.assertNotIn("intent", example)

    def test_sql_prompt_omits_unsafe_mysql_example_sql(self) -> None:
        prompt_builder = PromptBuilder(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=StaticExampleRegistry(
                [
                    {
                        "id": "inventory_mysql_example",
                        "question": "最新 OMS 库存库龄分布",
                        "sql": "SELECT DATE_FORMAT(report_month, '%Y%m') AS month_id, SUM(panel_qty) AS inventory_qty FROM oms_inventory GROUP BY DATE_FORMAT(report_month, '%Y%m') LIMIT 50",
                        "metrics": ["inventory_qty"],
                    }
                ]
            ),
        )
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="inventory_mysql_example",
                    score=3.0,
                    summary="mysql example",
                    matched_features=["metrics:inventory_qty"],
                )
            ]
        )

        prompt = prompt_builder.build_sql_prompt(query_plan, retrieval=retrieval, question="最新 OMS 库存")
        example = prompt["instructions"]["few_shot"]["retrieved_examples"][0]

        self.assertNotIn("sql", example)
        self.assertIn("sql_omitted_reason", example)


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
