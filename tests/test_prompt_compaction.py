from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from backend.app.models.semantic_types import FilterItem, TimeContext, TimeRange, VersionContext
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.models.session_state import PendingClarification, QueryTurnRecord, SessionState
from backend.app.models.api import ExecutionResponse
from backend.app.repositories.metadata_repository import FileMetadataRepository
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.metadata_service import MetadataService
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.example_factory import ExampleFactory
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_analysis_service import QuestionAnalysisService
from backend.app.services.semantic_runtime import SemanticRuntime


def sql_context(sql_context_value: SqlGenerationContext) -> SqlGenerationContext:
    return SqlGenerationContext(**sql_context_value.model_dump(mode="python"))


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


class MutableExampleRegistry(StaticExampleRegistry):
    @property
    def examples_template(self) -> list[dict]:
        return [dict(item) for item in self._examples]

    @examples_template.setter
    def examples_template(self, examples: list[dict]) -> None:
        self._examples = examples


class CountingExampleFactory:
    def __init__(self, delegate: ExampleFactory) -> None:
        self.delegate = delegate
        self.normalize_calls = 0

    def normalize(self, payload: dict):
        self.normalize_calls += 1
        return self.delegate.normalize(payload)


class CountingLLMClient:
    def __init__(self) -> None:
        self.enabled = True


class QuestionContextDetailLLMClient:
    def __init__(self) -> None:
        self.enabled = True

    def generate_question_context(self, prompt_payload, cancellation_token=None):
        return {
            "decision": "answerable",
            "context_relation": "follow_up" if prompt_payload.get("recent_turns") else "new",
            "subject_domain": "demand",
            "effective_question": prompt_payload.get("question", ""),
            "semantic_brief": "查询上一轮需求产品集合的具体型号列表，沿用上一轮来源、产品属性、月份和需求量过滤条件。",
        }


class ContextEchoDetailLLMClient(QuestionContextDetailLLMClient):
    def generate_question_context(self, prompt_payload, cancellation_token=None):
        return {
            "decision": "answerable",
            "context_relation": "follow_up",
            "subject_domain": "demand",
            "effective_question": prompt_payload.get("question", ""),
            "semantic_brief": "查询上一轮需求产品集合对应的具体型号，沿用月份、版本、产品属性和需求量过滤条件。",
        }


class PromptCompactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.domain_config = domain_config
        cls.semantic_runtime = SemanticRuntime(domain_config)
        cls.prompt_builder = PromptBuilder(semantic_runtime=cls.semantic_runtime)

    def test_question_context_prompt_uses_conversation_summary_and_table_metadata(self) -> None:
        session_state = SessionState(
            session_id="sess_rewrite_brief",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            last_semantic_brief="查询actual_input_qty；业务域是计划实际；按biz_month展示；条件为factory=ARRAY、IS_OXIDE=Y。",
            recent_turns=[
                QueryTurnRecord(
                    question="2026年Array工厂Oxide类产品，每个月分别投入多少物量",
                    semantic_brief="查询actual_input_qty；业务域是计划实际；按biz_month展示；条件为factory=ARRAY、IS_OXIDE=Y。",
                )
            ],
        )

        prompt = self.prompt_builder.build_question_context_prompt(
            question="XPS呢",
            session_state=session_state,
            parser_signals={"subject_domain": "plan_actual"},
        )

        self.assertIn("conversation_summary", prompt)
        self.assertIn("Oxide", prompt["conversation_summary"])
        self.assertIn("product_attributes", prompt["context_hints"]["table_fields"])
        table_fields = "\n".join(prompt["context_hints"]["table_fields"]["product_attributes"])
        self.assertIn("IS_XPS", table_fields)
        self.assertIn("IS_OXIDE", table_fields)

    def test_load_examples_caches_normalized_records_until_template_changes(self) -> None:
        registry = MutableExampleRegistry(
            [
                {
                    "id": "cache_example_1",
                    "question": "最新 OMS 库存",
                    "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 10 ROWS ONLY",
                    "subject_domain": "inventory",
                    "tags": ["oms_inventory"],
                }
            ]
        )
        prompt_builder = PromptBuilder(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=registry,
        )
        factory = CountingExampleFactory(prompt_builder.example_factory)
        prompt_builder.example_factory = factory

        first = prompt_builder._load_examples()
        second = prompt_builder._load_examples()

        self.assertIs(first, second)
        self.assertEqual(factory.normalize_calls, 1)

        registry.examples_template = [
            {
                "id": "cache_example_2",
                "question": "最新 OMS 库存库龄",
                "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 10 ROWS ONLY",
                "subject_domain": "inventory",
                "tags": ["oms_inventory"],
            }
        ]

        third = prompt_builder._load_examples()

        self.assertIsNot(third, first)
        self.assertEqual(set(third), {"cache_example_2"})
        self.assertEqual(factory.normalize_calls, 2)

    def test_retrieved_example_ranking_uses_bounded_fusion_boost(self) -> None:
        prompt_builder = PromptBuilder(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=StaticExampleRegistry(
                [
                    {
                        "id": "weak_raw_high_fusion",
                        "question": "最新 OMS 库存",
                        "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 10 ROWS ONLY",
                        "subject_domain": "inventory",
                        "tags": ["oms_inventory"],
                    },
                    {
                        "id": "strong_raw_low_fusion",
                        "question": "最新 OMS 库存",
                        "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 10 ROWS ONLY",
                        "subject_domain": "inventory",
                        "tags": ["oms_inventory"],
                    },
                ]
            ),
        )
        context = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            tables=["oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="weak_raw_high_fusion",
                    score=0.2,
                    fusion_score=0.1,
                    summary="vector evidence",
                ),
                RetrievalHit(
                    source_type="example",
                    source_id="strong_raw_low_fusion",
                    score=9.0,
                    fusion_score=0.0,
                    summary="weak normalized evidence",
                ),
            ]
        )

        examples = prompt_builder._select_retrieved_examples(
            context,
            retrieval,
            selected_sources=["oms_inventory"],
        )

        self.assertEqual([item["id"] for item in examples], ["weak_raw_high_fusion", "strong_raw_low_fusion"])

    def test_retrieval_boost_has_presence_floor_for_retrieved_examples_and_join_patterns(self) -> None:
        hit = RetrievalHit(
            source_type="example",
            source_id="retrieved_zero_fusion",
            score=0.2,
            fusion_score=0.0,
            summary="retrieved",
        )

        self.assertGreater(self.prompt_builder._retrieval_boost(hit), 0.0)

    def test_vector_only_example_passes_context_gate_via_fusion_score(self) -> None:
        # A vector-only example carries a tiny raw hit.score (cosine * 0.45),
        # so the old `hit.score >= 2.0` gate could never admit it without
        # structural overlap. The gate now keys off fusion_score, so an example
        # that is relevant only through the vector channel can still enter
        # scoring as long as it has a semantic matched_feature.
        example = self.prompt_builder.example_factory.normalize(
            {
                "id": "vector_only_example",
                "question": "最新 OMS 库存",
                "sql": "SELECT report_month FROM oms_inventory FETCH FIRST 10 ROWS ONLY",
                "subject_domain": "inventory",
                "tags": ["oms_inventory"],
            }
        )
        # Context shares no domain / table / metric / filter with the example,
        # so the only way through _retrieved_example_matches_context is the
        # final fusion-score + matched_feature fallback.
        context = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            tables=["monthly_plan_approved"],
        )
        vector_hit = RetrievalHit(
            source_type="example",
            source_id="vector_only_example",
            score=0.3,
            fusion_score=0.5,
            summary="vector evidence",
            matched_features=["metrics:report_month"],
        )

        self.assertTrue(
            self.prompt_builder._retrieved_example_matches_context(
                context,
                example,
                vector_hit,
                selected_sources=["monthly_plan_approved"],
            )
        )

        # The weakest hit in its bucket normalizes to fusion_score 0; it must
        # not slip through the gate on matched_features alone.
        weakest_hit = vector_hit.model_copy(update={"fusion_score": 0.0})
        self.assertFalse(
            self.prompt_builder._retrieved_example_matches_context(
                context,
                example,
                weakest_hit,
                selected_sources=["monthly_plan_approved"],
            )
        )

    def test_join_pattern_ranking_uses_bounded_fusion_boost_and_keeps_single_primary_pattern(self) -> None:
        prompt_builder = PromptBuilder(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=StaticExampleRegistry([]),
        )
        context = SqlGenerationContext(
            question_type="new",
            subject_domain="demand",
            tables=[],
            semantic_brief="",
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="join_pattern",
                    source_id="vector_primary_join",
                    score=0.2,
                    fusion_score=0.1,
                    summary="vector primary",
                    metadata={
                        "domains": ["demand"],
                        "tables": ["p_demand"],
                        "join_path": ["p_demand.FGCODE = product_attributes.product_ID"],
                        "notes": ["vector primary"],
                    },
                ),
                RetrievalHit(
                    source_type="join_pattern",
                    source_id="raw_score_join",
                    score=9.0,
                    fusion_score=0.0,
                    summary="raw score",
                    metadata={
                        "domains": ["demand"],
                        "tables": ["p_demand"],
                        "join_path": ["p_demand.FGCODE = sales_financial_perf.FGCODE"],
                        "notes": ["raw score"],
                    },
                ),
            ]
        )

        patterns = prompt_builder._selected_join_patterns(
            context,
            selected_sources=["p_demand"],
            retrieval=retrieval,
        )

        self.assertEqual([item["id"] for item in patterns], ["vector_primary_join"])

    def test_question_context_prompt_only_checks_natural_language_completeness(self) -> None:
        prompt = self.prompt_builder.build_question_context_prompt(
            question="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
            session_state=None,
            parser_signals={},
        )

        constraints = "\n".join(prompt["instructions"]["constraints"])

        self.assertIn("只做问题上下文整理，不生成 SQL", constraints)
        self.assertIn("只判断用户这句话和可用会话上下文是否足以形成完整自然语言问题", constraints)
        self.assertIn("不要判断业务知识、字段、表、计算方法或 SQL 是否足够", constraints)
        self.assertIn("如果用户表达替换、删除或新增条件，必须在 effective_question 中自然语言表达出来。", constraints)
        self.assertIn("短追问优先基于最近一轮用户问题补全", constraints)
        self.assertNotIn("matched_examples", prompt["context_hints"])
        self.assertNotIn("matched_join_patterns", prompt["context_hints"])

    def test_question_context_prompt_declares_supported_subject_domains(self) -> None:
        prompt = self.prompt_builder.build_question_context_prompt(
            question="202604月销售业绩和最新P版202604需求量差异最大的前10个FGCODE",
            session_state=None,
            parser_signals={},
        )

        instructions = prompt["instructions"]
        constraints = "\n".join(instructions["constraints"])

        self.assertEqual(
            instructions["subject_domain_values"],
            ["inventory", "demand", "plan_actual", "sales_financial", "dimension", "unknown"],
        )
        self.assertIn("subject_domain 只能输出 subject_domain_values 中的一个值", constraints)

    def test_question_context_prompt_includes_pending_clarification(self) -> None:
        session_state = SessionState(
            session_id="sess_pending_clarification",
            pending_clarification=PendingClarification(
                original_question="3月呢",
                effective_question="2026年3月Array工厂审批版投入物量与实际物量Gap和达成率",
                semantic_brief="查询2026年3月Array工厂审批版投入物量与实际物量的Gap和达成率。",
                clarification_question="你是指2026年3月吗？",
                reason="confirm_month_replacement",
            ),
        )

        prompt = self.prompt_builder.build_question_context_prompt(
            question="是的",
            session_state=session_state,
            parser_signals={},
        )

        pending = prompt["context_hints"]["pending_clarification"]
        constraints = "\n".join(prompt["instructions"]["constraints"])

        self.assertEqual(pending["original_question"], "3月呢")
        self.assertEqual(pending["clarification_question"], "你是指2026年3月吗？")
        self.assertIn("当前用户问题应优先视为对上一轮澄清问题的回答", constraints)
        self.assertIn("不要把“是的”“不是”“对”等确认词当成独立业务问题", constraints)

    def test_question_context_prompt_includes_prompt_diagnostics(self) -> None:
        session_state = SessionState(
            session_id="sess_prompt_diagnostics",
            subject_domain="plan_actual",
            pending_clarification=PendingClarification(
                original_question="3月呢",
                effective_question="2026年3月Array工厂审批版投入物量与实际物量Gap和达成率",
                semantic_brief="查询2026年3月Array工厂审批版投入物量与实际物量的Gap和达成率。",
                clarification_question="你是指2026年3月吗？",
                reason="confirm_month_replacement",
            ),
            recent_turns=[
                QueryTurnRecord(
                    question="2026年Array工厂Oxide类产品，每个月分别投入多少物量",
                    semantic_brief="查询actual_input_qty；业务域是计划实际；按biz_month展示。",
                )
            ],
        )

        prompt = self.prompt_builder.build_question_context_prompt(
            question="是的",
            session_state=session_state,
            parser_signals={"subject_domain": "plan_actual"},
        )

        diagnostics = prompt["prompt_diagnostics"]
        context_hints = prompt["context_hints"]
        focus_tables = context_hints.get("focus_tables", [])
        table_fields = context_hints.get("table_fields", {})

        self.assertEqual(diagnostics["conversation_summary_chars"], len(prompt["conversation_summary"]))
        self.assertEqual(diagnostics["recent_turn_count"], 1)
        self.assertEqual(
            diagnostics["business_knowledge_excerpt_chars"],
            len(context_hints["business_knowledge_excerpt"]),
        )
        self.assertEqual(diagnostics["focus_table_count"], len(focus_tables))
        self.assertEqual(diagnostics["table_field_table_count"], len(table_fields))
        self.assertEqual(
            diagnostics["table_field_count"],
            sum(len(fields) for fields in table_fields.values()),
        )
        self.assertTrue(diagnostics["has_pending_clarification"])

    def test_question_context_prompt_diagnostics_are_counts_not_raw_payload(self) -> None:
        prompt = self.prompt_builder.build_question_context_prompt(
            question="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
            session_state=None,
            parser_signals={},
        )

        diagnostics = prompt["prompt_diagnostics"]

        self.assertNotIn("conversation_summary", diagnostics)
        self.assertNotIn("recent_turns", diagnostics)
        self.assertNotIn("business_knowledge_excerpt", diagnostics)
        self.assertNotIn("table_fields", diagnostics)
        self.assertTrue(all(isinstance(value, (int, bool)) for value in diagnostics.values()))

    def test_sql_prompt_includes_semantic_brief(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="IS_XPS", op="=", value="Y")],
            semantic_brief="查询XPS类产品的actual_input_qty，并按biz_month展示。",
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="2026年Array工厂XPS类产品，每个月分别投入多少物量",
        )

        self.assertEqual(prompt["semantic_brief"], sql_context_value.semantic_brief)

    def test_question_context_prompt_facade_delegates_without_payload_change(self) -> None:
        session_state = SessionState(
            session_id="sess_prompt_split",
            subject_domain="plan_actual",
            recent_turns=[
                QueryTurnRecord(
                    question="2026年Array工厂Oxide类产品，每个月分别投入多少物量",
                    semantic_brief="查询actual_input_qty；业务域是计划实际；按biz_month展示。",
                )
            ],
        )

        kwargs = {
            "question": "XPS呢",
            "session_state": session_state,
            "parser_signals": {"subject_domain": "plan_actual"},
        }

        self.assertEqual(
            self.prompt_builder.build_question_context_prompt(**kwargs),
            self.prompt_builder.question_context_prompt_builder.build(**kwargs),
        )

    def test_sql_prompt_facade_delegates_without_payload_change(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"})],
            analysis_mode="distribution",
        )

        context = sql_context(sql_context_value)

        self.assertEqual(
            self.prompt_builder.build_sql_prompt(context, question="最新 OMS 库存库龄分布"),
            self.prompt_builder.sql_generation_prompt_builder.build(context, question="最新 OMS 库存库龄分布"),
        )

    def test_sql_prompt_context_assembler_feeds_sql_prompt_payload(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"})],
            analysis_mode="distribution",
        )

        context = sql_context(sql_context_value)
        bundle = self.prompt_builder.sql_prompt_context_assembler.assemble(context)
        prompt = self.prompt_builder.build_sql_prompt(context, question="最新 OMS 库存库龄分布")

        self.assertEqual(prompt["available_tables"], bundle.source_schemas)
        self.assertEqual(prompt["context_budget"], bundle.context_budget)
        self.assertEqual(prompt["context_summary"], bundle.context_summary)
        self.assertEqual(prompt["evidence_context"], bundle.evidence_context)
        self.assertEqual(prompt["instructions"]["sql_preferences"], bundle.sql_preferences)
        self.assertEqual(prompt["retrieval_context"]["business_knowledge"], bundle.business_knowledge)
        self.assertEqual(prompt["retrieval_context"]["examples"], bundle.retrieved_examples)
        self.assertEqual(prompt["retrieval_context"]["join_patterns"], bundle.selected_join_patterns)

    def test_sql_prompt_closes_schemas_for_selected_structured_knowledge(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="demand",
            tables=["p_demand"],
            semantic_brief="查询最新P版需求对应的销售业绩和财务业绩。",
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="table_schema",
                    source_id="p_demand",
                    score=4.0,
                    summary="P version demand",
                    metadata={"table": "p_demand", "domains": ["demand"]},
                )
            ]
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="最新P版需求对应的销售业绩和财务业绩",
        )

        self.assertIn("demand_fgcode_mapping", prompt["context_summary"]["business_knowledge_entry_ids"])
        self.assertIn("sales_financial_perf", prompt["available_tables"])
        self.assertIn("sales_qty (销售业绩)", prompt["available_tables"]["sales_financial_perf"]["columns"])

    def test_sql_prompt_keeps_retrieved_examples_matching_selected_sources_when_domain_unknown(self) -> None:
        question = "202604月销售业绩和最新P版202604需求量差异最大的前10个FGCODE"
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="unknown",
            tables=[],
            semantic_brief=question,
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="demand_sales_vs_latest_p_demand_diff_fgcode_202604_001",
                    score=8.0,
                    summary=question,
                    metadata={
                        "subject_domain": "demand",
                        "tables": ["p_demand", "sales_financial_perf"],
                    },
                )
            ]
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question=question,
        )

        self.assertIn(
            "demand_sales_vs_latest_p_demand_diff_fgcode_202604_001",
            prompt["context_summary"]["retrieved_example_ids"],
        )

    def test_sql_prompt_context_summary_includes_prompt_diagnostics(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            filters=[FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"})],
            analysis_mode="distribution",
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="最新 OMS 库存库龄分布",
        )
        summary = prompt["context_summary"]
        diagnostics = summary["prompt_diagnostics"]

        self.assertEqual(diagnostics["available_table_count"], len(prompt["available_tables"]))
        self.assertEqual(
            diagnostics["available_table_column_count"],
            sum(len(schema["columns"]) for schema in prompt["available_tables"].values()),
        )
        self.assertEqual(
            diagnostics["retrieved_example_count"],
            len(prompt["retrieval_context"]["examples"]),
        )
        self.assertEqual(
            diagnostics["join_pattern_count"],
            len(prompt["retrieval_context"]["join_patterns"]),
        )
        self.assertEqual(diagnostics["evidence_context_key_count"], len(prompt["evidence_context"]))
        self.assertGreater(diagnostics["prompt_payload_chars"], 0)
        self.assertGreaterEqual(diagnostics["retrieved_example_chars"], 0)

    def test_sql_prompt_diagnostics_are_counts_not_raw_payload(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="最新 OMS 库存",
        )
        diagnostics = prompt["context_summary"]["prompt_diagnostics"]

        self.assertNotIn("available_tables", diagnostics)
        self.assertNotIn("retrieval_context", diagnostics)
        self.assertNotIn("evidence_context", diagnostics)
        self.assertTrue(all(isinstance(value, int) for value in diagnostics.values()))

    def test_prompt_assets_keep_only_sql_generation_assets(self) -> None:
        assets = self.prompt_builder._prompt_assets()

        self.assertEqual(sorted(assets.keys()), [])

    def test_orchestrator_validation_repair_gate_only_allows_repairable_errors(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=[],
            sql="SELECT 1",
            llm_sql="SELECT 1",
            context_errors=[],
            sql_prompt={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "no_validation_errors")

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=["sql is empty"],
            sql=None,
            llm_sql=None,
            context_errors=[],
            sql_prompt={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sql_missing")

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=["sql references unknown sources: fake_table"],
            sql="SELECT * FROM fake_table",
            llm_sql="SELECT * FROM fake_table",
            context_errors=[],
            sql_prompt={},
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "repairable_validation_error")

        allowed, reason = orchestrator._should_repair_validation_errors(
            errors=["forbidden keyword detected:delete"],
            sql="DELETE FROM t",
            llm_sql="DELETE FROM t",
            context_errors=[],
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


    def test_sql_prompt_uses_available_tables_and_evidence_context(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            metrics=["inventory_qty"],
            tables=["oms_inventory"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="biz_month", op="latest_n", value={"count": 1, "source_table": "oms_inventory"})],
            analysis_mode="distribution",
            limit=50,
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="最新 OMS 库存",
        )

        self.assertIn("available_tables", prompt)
        self.assertIn("evidence_context", prompt)
        self.assertIn("retrieval_context", prompt)
        self.assertNotIn("sql_context", prompt)
        self.assertNotIn("tables_metadata", prompt)
        self.assertNotIn("allowed_fields", prompt)

        schema = prompt["available_tables"]["oms_inventory"]
        schema_text = "\n".join(schema["columns"])
        self.assertIn("product_ID", schema_text)
        self.assertIn("report_month", schema_text)
        self.assertIn("panel_qty", schema_text)
        self.assertIn("glass_qty", schema_text)
        self.assertIn("LGORT_DL", schema_text)
        self.assertIn("time_fields", schema)
        self.assertIn("relationships", schema)
        self.assertIn("table_schemas_count", prompt["context_summary"])
        self.assertEqual(prompt["context_summary"]["table_schema_columns_count"]["oms_inventory"], len(schema["columns"]))
        self.assertNotIn("tables_metadata_count", prompt["context_summary"])

    def test_sql_prompt_keeps_required_columns_when_sql_context_has_no_fields(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="demand",
            tables=[],
            metrics=[],
            dimensions=[],
            filters=[],
            semantic_brief="查询最近6个月TTL需求。",
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="demand_latest_p_recent6m_ttl_001",
                    score=5.0,
                    summary="recent 6 month ttl demand",
                    matched_features=["vector:0.900"],
                    metadata={"tables": ["p_demand", "v_demand"]},
                )
            ]
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="最近6个月TTL需求",
        )

        demand_schema_text = "\n".join(prompt["available_tables"]["p_demand"]["columns"])

        self.assertIn("PM_VERSION", demand_schema_text)
        self.assertIn("REQUIREMENT_QTY", demand_schema_text)
        self.assertIn("MONTH7", demand_schema_text)
        self.assertNotIn("field_resolution", prompt["evidence_context"])
        self.assertEqual(prompt["evidence_context"]["context_source"], "retrieval_evidence")

    def test_sql_prompt_preserves_demand_horizontal_and_product_attribute_columns(self) -> None:
        sql_context_value = SqlGenerationContext(
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

        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="demand_latest_p_202605_oxide_product_count_001",
                    score=5.0,
                    summary="latest P demand oxide product count example",
                    matched_features=[
                        "metrics:product_count,demand_qty",
                        "filters:PM_VERSION,biz_month,demand_qty",
                    ],
                )
            ]
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="最新P版，2026年5月Oxide产品数量是多少",
        )

        demand_schema_text = "\n".join(prompt["available_tables"]["p_demand"]["columns"])
        attributes_schema_text = "\n".join(prompt["available_tables"]["product_attributes"]["columns"])

        self.assertIn("PM_VERSION", demand_schema_text)
        self.assertIn("FGCODE", demand_schema_text)
        self.assertIn("IS_OXIDE", attributes_schema_text)
        self.assertIn("Y=是，N=否", attributes_schema_text)
        business_knowledge = prompt["retrieval_context"]["business_knowledge"]
        self.assertIn("IS_xxx 字段使用 Y/N 标记", business_knowledge)
        self.assertIn("目标需求月份仍然必须基于横表展开后的 demand_month 过滤", business_knowledge)
        self.assertNotIn("field_resolution", prompt["evidence_context"])
        examples = prompt["retrieval_context"]["examples"]
        self.assertEqual(examples[0]["id"], "demand_latest_p_202605_oxide_product_count_001")
        self.assertIn("demand_unpivot", examples[0]["sql"])
        self.assertIn("NVL(demand_unpivot.demand_qty, 0) > 0", examples[0]["sql"])

    def test_demand_product_count_question_also_requests_total_demand(self) -> None:
        llm_client = QuestionContextDetailLLMClient()
        analysis_service = QuestionAnalysisService(
            self.domain_config,
            llm_client,
            self.prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )

        classification, sql_context_value, _warnings = analysis_service.create_sql_context(
            "最新P版，2026年5月Oxide产品数量是多少"
        )

        self.assertEqual(classification.question_type, "new")
        self.assertEqual(sql_context_value.metrics, [])
        self.assertEqual(sql_context_value.filters, [])

    def test_sql_prompt_includes_physical_time_filter_examples_for_plan_actual_compare(self) -> None:
        sql_context_value = SqlGenerationContext(
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
            sql_context(sql_context_value),
            question="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        self.assertNotIn("field_resolution", prompt["evidence_context"])
        production_actuals_schema = "\n".join(prompt["available_tables"]["production_actuals"]["columns"])
        self.assertIn("FACTORY", production_actuals_schema)
        self.assertIn("act_type", production_actuals_schema)
        self.assertIn("plan_actual_approved_vs_actual_join", prompt["context_summary"]["join_pattern_ids"])

    def test_sql_prompt_expands_join_companion_tables_from_join_patterns(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            tables=["monthly_plan_approved"],
            semantic_brief="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        self.assertIn("monthly_plan_approved", prompt["available_tables"])
        self.assertIn("production_actuals", prompt["available_tables"])
        self.assertIn("plan_actual_approved_vs_actual_join", prompt["context_summary"]["join_pattern_ids"])

    def test_sql_prompt_reranks_retrieved_examples_by_table_evidence(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            tables=["monthly_plan_approved", "production_actuals"],
            semantic_brief="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
        )
        retrieval = RetrievalContext(
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="plan_actual_mdl_input_panel_top10_001",
                    score=4.0,
                    summary="MDL actual input top10",
                    matched_features=["keyword:4.000"],
                ),
                RetrievalHit(
                    source_type="example",
                    source_id="plan_actual_array_approved_vs_actual_input_gap_rate_001",
                    score=1.0,
                    summary="approved vs actual gap rate",
                    matched_features=["keyword:1.000", "metrics:input_gap_qty,input_achievement_rate"],
                ),
            ]
        )

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        examples = prompt["retrieval_context"]["examples"]
        self.assertGreaterEqual(len(examples), 2)
        self.assertEqual(examples[0]["id"], "plan_actual_array_approved_vs_actual_input_gap_rate_001")

    def test_sql_prompt_compacts_business_knowledge_and_examples(self) -> None:
        sql_context_value = SqlGenerationContext(
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

        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="最新 OMS 库存库龄分布",
        )
        business_knowledge = prompt["retrieval_context"]["business_knowledge"]

        self.assertLessEqual(len(business_knowledge), 1600)
        self.assertIn("不要自行追加 common_categories", business_knowledge)
        self.assertIn("默认取 oms_inventory.report_month 的最新月份", business_knowledge)
        self.assertIn(">12M = SIX_AGE_panel_qty + SEVEN_AGE_panel_qty + EUGHT_AGE_panel_qty", business_knowledge)
        self.assertNotIn("[join_pattern:", business_knowledge)

    def test_sql_prompt_retrieved_examples_omit_full_intent_text(self) -> None:
        sql_context_value = SqlGenerationContext(
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
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="最新 OMS 库存库龄分布",
        )
        examples = prompt["retrieval_context"]["examples"]

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
        self.assertEqual(example.subject_domain, "unknown")
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

        self.assertEqual(example.subject_domain, "unknown")
        self.assertEqual(example.tables, ["oms_inventory"])
        self.assertIn("unknown", example.coverage_tags)

    def test_complete_example_shape_is_rejected_as_template_input(self) -> None:
        factory = ExampleFactory(self.domain_config, self.semantic_runtime)

        with self.assertRaisesRegex(Exception, "Extra inputs are not permitted"):
            factory.normalize(
                {
                    "id": "old_complete_example",
                    "question": "最新 OMS 库存",
                    "normalized_question": "最新 oms 库存",
                    "intent": "complete shape",
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

    def test_metadata_update_document_triggers_retrieval_reload(self) -> None:
        with TemporaryDirectory() as temp_dir:
            knowledge_path = Path(temp_dir) / "business_knowledge.json"
            knowledge_path.write_text('{"entries": []}\n', encoding="utf-8")
            registry = MetadataRegistry(
                paths={
                    "examples_template": Path("examples/nl2sql_examples.template.json"),
                    "tables_metadata": Path("semantic/tables.json"),
                    "business_knowledge": knowledge_path,
                    "join_patterns": Path("semantic/join_patterns.json"),
                    "session_state_schema": Path("schemas/session_state.schema.json"),
                }
            )
            retrieval_service = type(
                "ReloadTrackingRetrievalService",
                (),
                {
                    "reload_calls": 0,
                    "reload": lambda self: setattr(self, "reload_calls", self.reload_calls + 1),
                },
            )()
            service = MetadataService(
                metadata_repository=FileMetadataRepository(registry),
                domain_config_loader=DomainConfigLoader(),
                audit_repository=EmptyAuditRepository(),
            )

            document = service.update_document(
                "business_knowledge",
                {"entries": [{"id": "kb_1", "notes": ["test"]}]},
                retrieval_service=retrieval_service,
            )

            self.assertEqual(document.name, "business_knowledge")
            self.assertEqual(retrieval_service.reload_calls, 1)
            self.assertEqual(registry.read("business_knowledge"), {"entries": [{"id": "kb_1", "notes": ["test"]}]})

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
        sql_context_value = SqlGenerationContext(
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

        prompt = prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="最新 OMS 库存",
        )
        example = prompt["retrieval_context"]["examples"][0]

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
        sql_context_value = SqlGenerationContext(
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

        prompt = prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            retrieval=retrieval,
            question="最新 OMS 库存",
        )
        example = prompt["retrieval_context"]["examples"][0]

        self.assertNotIn("sql", example)
        self.assertIn("sql_omitted_reason", example)


    def test_sql_prompt_fallback_constraints_reference_available_tables(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            tables=["oms_inventory"],
            metrics=["inventory_qty"],
        )
        prompt = self.prompt_builder.build_sql_prompt(
            sql_context(sql_context_value),
            question="最新 OMS 库存",
        )
        sql_text = "\n".join(prompt["instructions"]["constraints"])

        self.assertIn("available_tables", sql_text)
        self.assertIn("除法表达式必须用 NULLIF 或 CASE WHEN 防止除零", sql_text)
        self.assertIn("多表聚合对比时，优先先分别聚合到明确粒度", sql_text)
        self.assertNotIn("tables_metadata", sql_text)
        self.assertNotIn("sql_context.", sql_text)


if __name__ == "__main__":
    unittest.main()
