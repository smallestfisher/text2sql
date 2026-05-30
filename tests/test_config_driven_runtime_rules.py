from __future__ import annotations

from collections import OrderedDict
import unittest

from backend.app.models.query_plan import FilterItem, QueryPlan
from backend.app.models.classification import QuestionClassification
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.models.session_state import PendingClarification, SessionState
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.llm_client import LLMClient
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.sql_dialect import SqlDialect
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.session_state_service import SessionStateService


class StubRepairLLMClient(LLMClient):
    def __init__(self) -> None:
        self.model_name = "test-model"
        self.api_key = None
        self.api_base = None
        self.timeout_seconds = 20
        self.max_retries = 1
        self.repair_max_retries = 3
        self.cache_ttl_seconds = 300
        self.cache_max_entries = 256
        self._response_cache = OrderedDict()
        self._metrics = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        self.calls += 1
        self._record_metric(task_name, "provider_calls")
        if self.calls < 3:
            return "not valid sql"
        return "SELECT 1 FETCH FIRST 1 ROWS ONLY"

    def _is_single_sql_statement(self, sql: str) -> bool:
        return True


class StubCachedLLMClient(LLMClient):
    def __init__(self) -> None:
        self.model_name = "test-model"
        self.api_key = None
        self.api_base = None
        self.timeout_seconds = 20
        self.max_retries = 1
        self.repair_max_retries = 1
        self.cache_ttl_seconds = 60
        self.cache_max_entries = 4
        self._response_cache = OrderedDict()
        self._metrics = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        self.calls += 1
        self._record_metric(task_name, "provider_calls")
        return '{"decision":"answerable","subject_domain":"inventory","semantic_brief":"查询库存数量。"}'


class StubCachedSqlLLMClient(LLMClient):
    def __init__(self) -> None:
        self.model_name = "test-model"
        self.api_key = None
        self.api_base = None
        self.timeout_seconds = 20
        self.max_retries = 1
        self.repair_max_retries = 1
        self.cache_ttl_seconds = 60
        self.cache_max_entries = 4
        self._response_cache = OrderedDict()
        self._metrics = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = object()
        self.calls = 0

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        self.calls += 1
        self._record_metric(task_name, "provider_calls")
        return "SELECT product_ID FROM daily_inventory FETCH FIRST 10 ROWS ONLY"

    def _is_single_sql_statement(self, sql: str) -> bool:
        return True


class StubFollowUpXpsQuestionContextLLMClient:
    def __init__(self) -> None:
        self.enabled = True
        self.rewrite_calls = 0

    def generate_question_context(self, prompt_payload, cancellation_token=None):
        self.rewrite_calls += 1
        question = prompt_payload.get("question", "")
        if question == "XPS呢":
            return {
                "context_relation": "follow_up",
                "effective_question": "2026年Array工厂XPS类产品，每个月分别投入多少物量",
                "decision": "answerable",
                "subject_domain": "plan_actual",
                "semantic_brief": "查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。",
            }
        if question == "2025年呢":
            return {
                "context_relation": "follow_up",
                "effective_question": "2025年Array工厂Oxide类产品，每个月分别投入多少物量",
                "decision": "answerable",
                "subject_domain": "plan_actual",
                "semantic_brief": "查询2025年Array工厂Oxide类产品的实际投入物量，按月份展示。",
            }
        effective_question = question
        return {
            "context_relation": "new",
            "effective_question": effective_question,
            "decision": "answerable",
            "subject_domain": "plan_actual",
            "semantic_brief": effective_question,
        }


class ConfigDrivenRuntimeRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        domain_config = DomainConfigLoader().load()
        cls.domain_config = domain_config
        cls.semantic_runtime = SemanticRuntime(domain_config)


    def test_llm_question_context_generation_uses_prompt_cache(self) -> None:
        client = StubCachedLLMClient()
        prompt = {"question": "查询库存", "context_hints": {"focus_tables": ["oms_inventory"]}}

        first = client.generate_question_context(prompt)
        second = client.generate_question_context(prompt)

        self.assertEqual(client.calls, 1)
        self.assertFalse(first.get("cache_hit", False))
        self.assertTrue(second.get("cache_hit"))
        self.assertEqual(second["semantic_brief"], "查询库存数量。")
        health = client.health()
        self.assertEqual(health["cache_entries"], 1)
        self.assertEqual(health["metrics"]["question_context"]["requests"], 2)
        self.assertEqual(health["metrics"]["question_context"]["provider_calls"], 1)
        self.assertEqual(health["metrics"]["question_context"]["cache_hits"], 1)

    def test_llm_sql_generation_uses_prompt_cache(self) -> None:
        client = StubCachedSqlLLMClient()
        prompt = {"question": "查询库存", "available_tables": {"daily_inventory": {"columns": ["product_ID"]}}}

        first = client.generate_sql_hint(prompt)
        second = client.generate_sql_hint(prompt)

        self.assertEqual(client.calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(second, "SELECT product_ID FROM daily_inventory FETCH FIRST 10 ROWS ONLY;")
        health = client.health()
        self.assertEqual(health["cache_entries"], 1)
        self.assertEqual(health["metrics"]["sql"]["requests"], 2)
        self.assertEqual(health["metrics"]["sql"]["provider_calls"], 1)
        self.assertEqual(health["metrics"]["sql"]["cache_hits"], 1)

    def test_llm_cache_can_be_disabled(self) -> None:
        client = StubCachedLLMClient()
        client.cache_ttl_seconds = 0
        prompt = {"question": "查询库存"}

        client.generate_question_context(prompt)
        client.generate_question_context(prompt)

        self.assertEqual(client.calls, 2)
        health = client.health()
        self.assertEqual(health["cache_entries"], 0)
        self.assertEqual(health["metrics"]["question_context"]["requests"], 2)
        self.assertEqual(health["metrics"]["question_context"]["provider_calls"], 2)
        self.assertEqual(health["metrics"]["question_context"].get("cache_hits", 0), 0)

    def test_repair_sql_uses_dedicated_retry_budget(self) -> None:
        client = StubRepairLLMClient()

        repaired_sql = client.repair_sql(
            prompt_payload={"query_plan": {"tables": ["demo_table"]}},
            sql="SELECT 1;",
            errors=["sql is missing limit"],
            warnings=[],
        )

        self.assertEqual(client.calls, 3)
        self.assertEqual(repaired_sql, "SELECT 1 FETCH FIRST 1 ROWS ONLY;")
        health = client.health()
        self.assertEqual(health["repair_max_retries"], 3)
        self.assertEqual(health["metrics"]["repair"]["requests"], 1)
        self.assertEqual(health["metrics"]["repair"]["provider_calls"], 3)

    def test_schema_boundary_does_not_apply_inventory_source_rules(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="inventory",
            tables=["daily_inventory", "oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertEqual(sanitized.tables, ["daily_inventory", "oms_inventory"])
        self.assertEqual(sanitized.filters, [FilterItem(field="source_table", op="=", value="oms_inventory")])

    def test_schema_boundary_does_not_apply_demand_post_process_rules(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="demand",
            tables=["p_demand"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="source_table", op="=", value="p_demand"),
                FilterItem(field="demand_month", op="=", value="202604"),
            ],
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertEqual(sanitized.tables, ["p_demand"])
        self.assertEqual(sanitized.dimensions, ["biz_month"])
        self.assertEqual(
            sanitized.filters,
            [
                FilterItem(field="source_table", op="=", value="p_demand"),
                FilterItem(field="demand_month", op="=", value="202604"),
            ],
        )

    def test_plan_actual_support_table_rule_appends_product_attributes(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            dimensions=["common_categories"],
        )

        sanitized = self.semantic_runtime.sanitize_query_plan(query_plan)

        self.assertEqual(sanitized.tables, ["production_actuals"])

    def test_llm_filter_string_follow_up_replaces_product_attribute_flag(self) -> None:
        llm_client = StubFollowUpXpsQuestionContextLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_xps",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="factory", op="=", value="ARRAY"),
                FilterItem(field="act_type", op="=", value="投入"),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
            ],
        )

        trace = planner.build_planning_trace(
            question="XPS呢",
            session_state=session_state,
        )
        query_plan = planner.build_plan_shell(
            classification=trace["classification"],
            session_state=session_state,
            question_context=trace["question_context"],
        )

        self.assertEqual(
            sorted(trace.keys()),
            ["classification", "effective_question", "original_question", "question_context", "warnings"],
        )
        self.assertEqual(trace["effective_question"], "2026年Array工厂XPS类产品，每个月分别投入多少物量")
        self.assertEqual(trace["question_context"].context_relation, "follow_up")
        self.assertEqual(trace["question_context"].semantic_brief, "查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。")
        self.assertEqual(llm_client.rewrite_calls, 1)
        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertFalse(trace["classification"].inherit_context)
        self.assertEqual(query_plan.semantic_brief, trace["question_context"].semantic_brief)
        self.assertEqual(query_plan.filters, [])
        self.assertEqual(query_plan.tables, [])

    def test_question_context_can_replace_year_in_follow_up(self) -> None:
        llm_client = StubFollowUpXpsQuestionContextLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_year",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="biz_date", op="between", value=["2026-01-01", "2026-12-31"]),
                FilterItem(field="factory", op="=", value="ARRAY"),
                FilterItem(field="act_type", op="=", value="投入"),
                FilterItem(field="IS_OXIDE", op="=", value="Y"),
            ],
        )

        trace = planner.build_planning_trace(
            question="2025年呢",
            session_state=session_state,
        )
        query_plan = planner.build_plan_shell(
            classification=trace["classification"],
            session_state=session_state,
            question_context=trace["question_context"],
        )

        self.assertEqual(trace["effective_question"], "2025年Array工厂Oxide类产品，每个月分别投入多少物量")
        self.assertEqual(trace["question_context"].context_relation, "follow_up")
        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertFalse(trace["classification"].inherit_context)
        self.assertEqual(query_plan.filters, [])

    def test_query_planner_trace_uses_question_context_shape(self) -> None:
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=StubFollowUpXpsQuestionContextLLMClient(),
            prompt_builder=prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )
        question = "XPS呢"

        trace = planner.build_planning_trace(question=question)

        self.assertEqual(
            sorted(trace.keys()),
            ["classification", "effective_question", "original_question", "question_context", "warnings"],
        )

    def test_session_state_stores_semantic_brief_in_recent_turns(self) -> None:
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="IS_XPS", op="=", value="Y")],
            semantic_brief="查询XPS类产品的actual_input_qty，并按biz_month展示。",
        )

        state = SessionStateService().build_next_state(
            query_plan=query_plan,
            previous_state=None,
            question="2026年Array工厂XPS类产品，每个月分别投入多少物量",
        )

        self.assertEqual(state.last_semantic_brief, query_plan.semantic_brief)
        self.assertEqual(state.recent_turns[-1].semantic_brief, query_plan.semantic_brief)

    def test_answerable_turn_clears_pending_clarification(self) -> None:
        previous_state = SessionState(
            session_id="sess_pending",
            pending_clarification=PendingClarification(
                original_question="3月呢",
                clarification_question="你是指2026年3月吗？",
            ),
        )
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="plan_actual",
            semantic_brief="查询2026年3月Array工厂审批版投入物量与实际物量的Gap和达成率。",
        )

        state = SessionStateService().build_next_state(
            query_plan=query_plan,
            previous_state=previous_state,
            question="2026年3月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        self.assertIsNone(state.pending_clarification)

    def test_plan_shell_uses_question_context_only(self) -> None:
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        planner = QueryPlanner(
            domain_config=self.domain_config,
            llm_client=StubFollowUpXpsQuestionContextLLMClient(),
            prompt_builder=prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )

        query_plan = planner.build_plan_shell(
            classification=QuestionClassification(question_type="new", subject_domain="plan_actual"),
        )

        self.assertEqual(query_plan.filters, [])

    def test_domain_backfill_ignores_non_schema_domain_labels(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.query_planner = type(
            "QueryPlannerStub",
            (),
            {"semantic_runtime": self.semantic_runtime},
        )()
        classification = type(
            "ClassificationStub",
            (),
            {"subject_domain": "oms库存"},
        )()
        question_context = type(
            "QuestionContextStub",
            (),
            {"subject_domain": "oms库存"},
        )()
        query_plan = QueryPlan(
            question_type="new",
            subject_domain="unknown",
            tables=["oms_inventory"],
        )
        retrieval = type(
            "RetrievalStub",
            (),
            {
                "domains": ["oms库存"],
                "hits": [type("HitStub", (), {"metadata": {"tables": ["oms_inventory"]}})()],
            },
        )()

        _, resolved_plan, _ = orchestrator._apply_retrieval_domain_to_plan_shell(
            classification=classification,
            query_plan=query_plan,
            question_context=question_context,
            retrieval=retrieval,
        )

        self.assertEqual(resolved_plan.subject_domain, "inventory")

        state = SessionStateService().build_next_state(
            query_plan=resolved_plan,
            previous_state=None,
            question="oms库存，近6个月库存变化趋势",
        )

        self.assertEqual(state.subject_domain, "inventory")

    def test_retrieval_support_reopens_complete_question_context_clarification(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        classification = QuestionClassification(
            question_type="clarification_needed",
            subject_domain="unknown",
            need_clarification=True,
            clarification_question="请确认版本字段的判定口径。",
            reason_code="question_context_clarification",
        )
        query_plan = QueryPlan(
            question_type="clarification_needed",
            subject_domain="demand",
            tables=["p_demand"],
            need_clarification=True,
            clarification_question="请确认版本字段的判定口径。",
            semantic_brief="查询最新5版P版需求中202603需求量最高的FGCODE。",
        )
        question_context = type(
            "QuestionContextStub",
            (),
            {
                "decision": "clarification_needed",
                "context_relation": "ambiguous",
                "effective_question": "",
                "semantic_brief": "查询最新5版P版需求中202603需求量最高的FGCODE。",
                "model_copy": lambda self, update=None, deep=False: type(
                    "QuestionContextStub",
                    (),
                    {
                        **self.__dict__,
                        **(update or {}),
                        "model_copy": self.model_copy,
                    },
                )(),
            },
        )()
        retrieval = RetrievalContext(
            domains=["demand"],
            hits=[
                RetrievalHit(
                    source_type="example",
                    source_id="demand_latest5_p_202604_top_fgcode_001",
                    score=10.0,
                    summary="最新5版p版需求中，202604需求量最多的fgcode是哪一个",
                    metadata={"subject_domain": "demand", "tables": ["p_demand"]},
                )
            ],
        )

        resolved_classification, resolved_plan, resolved_context = orchestrator._apply_retrieval_support_to_clarification(
            classification=classification,
            query_plan=query_plan,
            question_context=question_context,
            retrieval=retrieval,
            original_question="我是指的最新5版p版需求中，202603需求量最多的fgcode是哪一个",
        )

        self.assertFalse(resolved_classification.need_clarification)
        self.assertFalse(resolved_plan.need_clarification)
        self.assertEqual(resolved_classification.question_type, "new")
        self.assertEqual(resolved_plan.question_type, "new")
        self.assertEqual(resolved_context.decision, "answerable")
        self.assertEqual(
            resolved_context.effective_question,
            "我是指的最新5版p版需求中，202603需求量最多的fgcode是哪一个",
        )

    def test_terminal_clarification_is_saved_as_pending_context(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        previous_state = SessionState(session_id="sess_pending")
        question_context = type(
            "QuestionContextStub",
            (),
            {
                "effective_question": "2026年3月Array工厂审批版投入物量与实际物量Gap和达成率",
                "semantic_brief": "查询2026年3月Array工厂审批版投入物量与实际物量的Gap和达成率。",
                "clarification_question": "你是指2026年3月吗？",
                "reason": "confirm_month_replacement",
            },
        )()
        classification = QuestionClassification(
            question_type="clarification_needed",
            subject_domain="plan_actual",
            need_clarification=True,
            clarification_question="你是指2026年3月吗？",
        )
        request = type("RequestStub", (), {"question": "3月呢"})()

        state = orchestrator._terminal_session_state(
            session_state=previous_state,
            session_id="sess_pending",
            request=request,
            question_context=question_context,
            classification=classification,
        )

        self.assertIsNotNone(state.pending_clarification)
        self.assertEqual(state.pending_clarification.original_question, "3月呢")
        self.assertEqual(state.pending_clarification.clarification_question, "你是指2026年3月吗？")
        self.assertEqual(
            state.pending_clarification.effective_question,
            "2026年3月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

if __name__ == "__main__":
    unittest.main()
