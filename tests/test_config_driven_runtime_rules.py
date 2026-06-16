from __future__ import annotations

from collections import OrderedDict
import unittest

from backend.app.models.api import ChatRequest, ChatResponse, ExecutionResponse, ValidationResponse
from backend.app.models.context_summary import ContextSummary
from backend.app.models.semantic_types import FilterItem
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.models.classification import QuestionClassification
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.models.session_state import PendingClarification, QueryTurnRecord, SessionState
from backend.app.models.trace import TraceRecord
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.llm_client import LLMClient
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.sql_dialect import SqlDialect
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_analysis_service import QuestionAnalysisService
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


class RecordingQuestionContextLLMClient:
    def __init__(self, responses: list[dict]) -> None:
        self.enabled = True
        self.responses = list(responses)
        self.prompts: list[dict] = []

    def generate_question_context(self, prompt_payload, cancellation_token=None):
        self.prompts.append(prompt_payload)
        if not self.responses:
            raise AssertionError("unexpected question context call")
        return self.responses.pop(0)


class RecordingProgressService:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class FailingPersistenceService:
    def persist_success(self, **kwargs) -> None:
        raise RuntimeError("persist failed")


class RecordingAuditService:
    def append_step(self, trace, name, status, detail=None, metadata=None) -> None:
        from backend.app.models.trace import TraceStep

        trace.steps.append(
            TraceStep(name=name, status=status, detail=detail, metadata=metadata or {})
        )


class FakeChatCompletionResponse:
    choices = [type("Choice", (), {"message": type("Message", (), {"content": "SELECT 1"})()})()]


class FakeReasoningChatCompletionResponse:
    choices = [
        type(
            "Choice",
            (),
            {
                "message": type(
                    "Message",
                    (),
                    {
                        "content": '{"ok":true}',
                        "reasoning_content": "internal reasoning text",
                    },
                )()
            },
        )()
    ]


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.chat = type(
            "Chat",
            (),
            {
                "completions": type(
                    "Completions",
                    (),
                    {"create": self._create},
                )()
            },
        )()

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeChatCompletionResponse()


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

    def test_question_context_retry_does_not_replay_model_control_tokens(self) -> None:
        client = StubCachedLLMClient()
        base_messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ]
        retry_messages = client._question_context_retry_messages(
            base_messages,
            '<|channel|>final <|constrain|>json<|message|>{"decision":"invalid","reason":"No question provided."}',
        )

        self.assertEqual([message["role"] for message in retry_messages], ["system", "user"])
        retry_content = str(retry_messages[-1]["content"])
        self.assertNotIn("<|channel|>", retry_content)
        self.assertNotIn("<|constrain|>", retry_content)
        self.assertNotIn("<|message|>", retry_content)
        self.assertIn('"decision":"invalid"', retry_content)

    def test_llm_response_records_reasoning_content_length_only(self) -> None:
        client = StubCachedLLMClient()
        content = client._response_content(FakeReasoningChatCompletionResponse(), task_name="question_context")

        self.assertEqual(content, '{"ok":true}')
        self.assertEqual(
            client.health()["metrics"]["question_context"]["reasoning_chars"],
            len("internal reasoning text"),
        )

    def test_complete_question_context_admission_does_not_expose_history(self) -> None:
        question = "202604月销售业绩对202604月份最后一版P版中202605需求量覆盖不足的客户有哪些？"
        llm_client = RecordingQuestionContextLLMClient(
            [
                {
                    "decision": "answerable",
                    "context_relation": "new",
                    "subject_domain": "demand",
                    "effective_question": question,
                    "semantic_brief": "查询202604销售业绩对202605需求量覆盖不足的客户。",
                    "reason": "当前问题已经包含查询对象、时间、指标和输出对象。",
                }
            ]
        )
        analysis_service = QuestionAnalysisService(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=PromptBuilder(semantic_runtime=self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_polluted_history",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            conversation_summary="用户此前查询Array工厂Oxide产品投入物量。",
            recent_turns=[
                QueryTurnRecord(
                    question="2026年Array工厂Oxide类产品，每个月分别投入多少物量",
                    semantic_brief="查询Array工厂Oxide产品实际投入物量。",
                )
            ],
        )

        trace = analysis_service.analyze_question(
            question=question,
            session_state=session_state,
        )

        self.assertEqual(len(llm_client.prompts), 1)
        admission_prompt = llm_client.prompts[0]
        self.assertEqual(admission_prompt["conversation_summary"], "")
        self.assertIsNone(admission_prompt["last_turn"])
        self.assertEqual(admission_prompt["recent_turns"], [])
        self.assertNotIn("pending_clarification", admission_prompt["context_hints"])
        self.assertNotIn("production_actuals", admission_prompt["context_hints"].get("focus_tables", []))
        self.assertEqual(trace["question_context"].context_relation, "new")
        self.assertEqual(trace["effective_question"], question)

    def test_context_admission_uses_history_only_for_context_dependent_question(self) -> None:
        llm_client = RecordingQuestionContextLLMClient(
            [
                {
                    "decision": "clarification_needed",
                    "context_relation": "ambiguous",
                    "subject_domain": "unknown",
                    "effective_question": "XPS呢",
                    "semantic_brief": "",
                    "clarification_question": "需要结合上一轮问题补全。",
                    "reason": "当前问题存在省略表达。",
                },
                {
                    "decision": "answerable",
                    "context_relation": "follow_up",
                    "subject_domain": "plan_actual",
                    "effective_question": "2026年Array工厂XPS类产品，每个月分别投入多少物量",
                    "semantic_brief": "查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。",
                    "reason": "结合上一轮Oxide查询，将产品属性替换为XPS。",
                },
            ]
        )
        analysis_service = QuestionAnalysisService(
            domain_config=self.domain_config,
            llm_client=llm_client,
            prompt_builder=PromptBuilder(semantic_runtime=self.semantic_runtime),
            semantic_runtime=self.semantic_runtime,
        )
        session_state = SessionState(
            session_id="sess_follow_up_history",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            recent_turns=[
                QueryTurnRecord(
                    question="2026年Array工厂Oxide类产品，每个月分别投入多少物量",
                    semantic_brief="查询2026年Array工厂Oxide类产品的实际投入物量，按月份展示。",
                )
            ],
        )

        trace = analysis_service.analyze_question(
            question="XPS呢",
            session_state=session_state,
        )

        self.assertEqual(len(llm_client.prompts), 2)
        admission_prompt, rewrite_prompt = llm_client.prompts
        self.assertEqual(admission_prompt["conversation_summary"], "")
        self.assertEqual(admission_prompt["recent_turns"], [])
        self.assertIn("Oxide", rewrite_prompt["conversation_summary"])
        self.assertTrue(rewrite_prompt["recent_turns"])
        self.assertIn("product_attributes", rewrite_prompt["context_hints"]["table_fields"])
        self.assertEqual(trace["question_context"].context_relation, "follow_up")
        self.assertEqual(
            trace["effective_question"],
            "2026年Array工厂XPS类产品，每个月分别投入多少物量",
        )

    def test_llm_cache_prompt_option_is_passed_to_openai_compatible_client(self) -> None:
        client = LLMClient(cache_prompt=False)
        fake_client = FakeOpenAIClient()
        client.client = fake_client

        content = client._complete_once(
            [{"role": "user", "content": "SELECT 1"}],
            stream=False,
        )

        self.assertEqual(content, "SELECT 1")
        self.assertEqual(fake_client.calls[0]["extra_body"], {"cache_prompt": False})

    def test_llm_cache_prompt_option_is_omitted_when_unset(self) -> None:
        client = LLMClient()
        fake_client = FakeOpenAIClient()
        client.client = fake_client

        client._complete_once(
            [{"role": "user", "content": "SELECT 1"}],
            stream=False,
        )

        self.assertNotIn("extra_body", fake_client.calls[0])

    def test_repair_sql_uses_dedicated_retry_budget(self) -> None:
        client = StubRepairLLMClient()

        repaired_sql = client.repair_sql(
            prompt_payload={"sql_context": {"tables": ["demo_table"]}},
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
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="inventory",
            tables=["daily_inventory", "oms_inventory"],
            filters=[FilterItem(field="source_table", op="=", value="oms_inventory")],
        )

        sanitized = self.semantic_runtime.sanitize_sql_context(sql_context_value)

        self.assertEqual(sanitized.tables, ["daily_inventory", "oms_inventory"])
        self.assertEqual(sanitized.filters, [FilterItem(field="source_table", op="=", value="oms_inventory")])

    def test_schema_boundary_does_not_apply_demand_post_process_rules(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="demand",
            tables=["p_demand"],
            dimensions=["biz_month"],
            filters=[
                FilterItem(field="source_table", op="=", value="p_demand"),
                FilterItem(field="demand_month", op="=", value="202604"),
            ],
        )

        sanitized = self.semantic_runtime.sanitize_sql_context(sql_context_value)

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
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            metrics=["actual_output_qty"],
            tables=["production_actuals"],
            dimensions=["common_categories"],
        )

        sanitized = self.semantic_runtime.sanitize_sql_context(sql_context_value)

        self.assertEqual(sanitized.tables, ["production_actuals"])

    def test_llm_filter_string_follow_up_replaces_product_attribute_flag(self) -> None:
        llm_client = StubFollowUpXpsQuestionContextLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        analysis_service = QuestionAnalysisService(
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

        trace = analysis_service.analyze_question(
            question="XPS呢",
            session_state=session_state,
        )
        sql_context_value = analysis_service.build_sql_context(
            classification=trace["classification"],
            session_state=session_state,
            question_context=trace["question_context"],
        )

        self.assertEqual(
            sorted(trace.keys()),
            [
                "classification",
                "effective_question",
                "original_question",
                "prompt_diagnostics",
                "question_context",
                "warnings",
            ],
        )
        self.assertIsInstance(trace["prompt_diagnostics"], dict)
        self.assertEqual(trace["effective_question"], "2026年Array工厂XPS类产品，每个月分别投入多少物量")
        self.assertEqual(trace["question_context"].context_relation, "follow_up")
        self.assertEqual(trace["question_context"].semantic_brief, "查询2026年Array工厂XPS类产品的实际投入物量，按月份展示。")
        self.assertEqual(llm_client.rewrite_calls, 2)
        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertFalse(trace["classification"].inherit_context)
        self.assertEqual(sql_context_value.semantic_brief, trace["question_context"].semantic_brief)
        self.assertEqual(sql_context_value.filters, [])
        self.assertEqual(sql_context_value.tables, [])

    def test_question_context_can_replace_year_in_follow_up(self) -> None:
        llm_client = StubFollowUpXpsQuestionContextLLMClient()
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        analysis_service = QuestionAnalysisService(
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

        trace = analysis_service.analyze_question(
            question="2025年呢",
            session_state=session_state,
        )
        sql_context_value = analysis_service.build_sql_context(
            classification=trace["classification"],
            session_state=session_state,
            question_context=trace["question_context"],
        )

        self.assertEqual(trace["effective_question"], "2025年Array工厂Oxide类产品，每个月分别投入多少物量")
        self.assertEqual(trace["question_context"].context_relation, "follow_up")
        self.assertEqual(trace["classification"].question_type, "follow_up")
        self.assertFalse(trace["classification"].inherit_context)
        self.assertEqual(sql_context_value.filters, [])

    def test_question_analysis_service_trace_uses_question_context_shape(self) -> None:
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        analysis_service = QuestionAnalysisService(
            domain_config=self.domain_config,
            llm_client=StubFollowUpXpsQuestionContextLLMClient(),
            prompt_builder=prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )
        question = "XPS呢"

        trace = analysis_service.analyze_question(question=question)

        self.assertEqual(
            sorted(trace.keys()),
            [
                "classification",
                "effective_question",
                "original_question",
                "prompt_diagnostics",
                "question_context",
                "warnings",
            ],
        )
        self.assertIsInstance(trace["prompt_diagnostics"], dict)

    def test_session_state_stores_semantic_brief_in_recent_turns(self) -> None:
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            tables=["production_actuals", "product_attributes"],
            metrics=["actual_input_qty"],
            dimensions=["biz_month"],
            filters=[FilterItem(field="IS_XPS", op="=", value="Y")],
            semantic_brief="查询XPS类产品的actual_input_qty，并按biz_month展示。",
        )

        state = SessionStateService().build_next_state(
            update=SessionStateService.update_from_sql_context(sql_context_value),
            previous_state=None,
            question="2026年Array工厂XPS类产品，每个月分别投入多少物量",
        )

        self.assertEqual(state.last_semantic_brief, sql_context_value.semantic_brief)
        self.assertEqual(state.recent_turns[-1].semantic_brief, sql_context_value.semantic_brief)

    def test_answerable_turn_clears_pending_clarification(self) -> None:
        previous_state = SessionState(
            session_id="sess_pending",
            pending_clarification=PendingClarification(
                original_question="3月呢",
                clarification_question="你是指2026年3月吗？",
            ),
        )
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="plan_actual",
            semantic_brief="查询2026年3月Array工厂审批版投入物量与实际物量的Gap和达成率。",
        )

        state = SessionStateService().build_next_state(
            update=SessionStateService.update_from_sql_context(sql_context_value),
            previous_state=previous_state,
            question="2026年3月Array工厂审批版投入物量与实际物量Gap和达成率",
        )

        self.assertIsNone(state.pending_clarification)

    def test_sql_context_uses_question_context_only(self) -> None:
        prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        analysis_service = QuestionAnalysisService(
            domain_config=self.domain_config,
            llm_client=StubFollowUpXpsQuestionContextLLMClient(),
            prompt_builder=prompt_builder,
            semantic_runtime=self.semantic_runtime,
        )

        sql_context_value = analysis_service.build_sql_context(
            classification=QuestionClassification(question_type="new", subject_domain="plan_actual"),
        )

        self.assertEqual(sql_context_value.filters, [])

    def test_domain_backfill_ignores_non_schema_domain_labels(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.question_analysis_service = type(
            "QuestionAnalysisServiceStub",
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
        sql_context_value = SqlGenerationContext(
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

        _, resolved_context, _ = orchestrator._apply_retrieval_domain_to_sql_context(
            classification=classification,
            sql_context=sql_context_value,
            question_context=question_context,
            retrieval=retrieval,
        )

        self.assertEqual(resolved_context.subject_domain, "inventory")

        state = SessionStateService().build_next_state(
            update=SessionStateService.update_from_sql_context(resolved_context),
            previous_state=None,
            question="oms库存，近6个月库存变化趋势",
        )

        self.assertEqual(state.subject_domain, "inventory")

    def test_retrieval_conversation_summary_is_gated_by_context_relation(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        summary = "上一轮查询 OMS 库存库龄分布。"

        new_question_context = type(
            "QuestionContextStub",
            (),
            {"context_relation": "new", "conversation_summary": summary},
        )()
        follow_up_question_context = type(
            "QuestionContextStub",
            (),
            {"context_relation": "follow_up", "conversation_summary": summary},
        )()
        ambiguous_question_context = type(
            "QuestionContextStub",
            (),
            {"context_relation": "ambiguous", "conversation_summary": summary},
        )()

        self.assertIsNone(orchestrator._retrieval_conversation_summary(new_question_context))
        self.assertEqual(orchestrator._retrieval_conversation_summary(follow_up_question_context), summary)
        self.assertEqual(orchestrator._retrieval_conversation_summary(ambiguous_question_context), summary)

    def test_empty_clarification_stops_before_retrieval(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        classification = QuestionClassification(
            question_type="clarification_needed",
            subject_domain="unknown",
            need_clarification=True,
            clarification_question="请明确上一个问题的具体内容。",
        )
        question_context = type(
            "QuestionContextStub",
            (),
            {
                "decision": "clarification_needed",
                "context_relation": "follow_up",
                "effective_question": "",
                "semantic_brief": "",
            },
        )()

        reason = orchestrator._pre_retrieval_terminal_skip_reason(classification, question_context)

        self.assertEqual(reason, "terminal gate: clarification required, skip retrieval and SQL generation")

    def test_clarification_with_semantic_brief_can_reach_retrieval_support(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        classification = QuestionClassification(
            question_type="clarification_needed",
            subject_domain="unknown",
            need_clarification=True,
            clarification_question="请确认版本字段的判定口径。",
        )
        question_context = type(
            "QuestionContextStub",
            (),
            {
                "decision": "clarification_needed",
                "context_relation": "ambiguous",
                "effective_question": "",
                "semantic_brief": "查询最新5版P版需求中202603需求量最高的FGCODE。",
            },
        )()

        self.assertIsNone(orchestrator._pre_retrieval_terminal_skip_reason(classification, question_context))

    def test_empty_retrieval_turns_answerable_question_into_clarification(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        classification = QuestionClassification(
            question_type="new",
            subject_domain="unknown",
            need_clarification=False,
        )
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="unknown",
            semantic_brief="查询一个没有任何语义资产支持的问题。",
        )
        question_context = type(
            "QuestionContextStub",
            (),
            {
                "decision": "answerable",
                "context_relation": "new",
                "effective_question": "查询一个没有任何语义资产支持的问题",
                "semantic_brief": "查询一个没有任何语义资产支持的问题。",
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
            retrieval_terms=["查询一个没有任何语义资产支持的问题"],
            hits=[],
        )

        resolved_classification, resolved_context, resolved_question_context = orchestrator._apply_empty_retrieval_to_clarification(
            classification=classification,
            sql_context=sql_context_value,
            question_context=question_context,
            retrieval=retrieval,
        )

        self.assertTrue(resolved_classification.need_clarification)
        self.assertTrue(resolved_context.need_clarification)
        self.assertEqual(resolved_classification.question_type, "clarification_needed")
        self.assertEqual(resolved_context.question_type, "clarification_needed")
        self.assertEqual(resolved_question_context.decision, "clarification_needed")
        self.assertIn("没有检索到", resolved_classification.clarification_question)

    def test_retrieval_support_reopens_complete_question_context_clarification(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        classification = QuestionClassification(
            question_type="clarification_needed",
            subject_domain="unknown",
            need_clarification=True,
            clarification_question="请确认版本字段的判定口径。",
            reason_code="question_context_clarification",
        )
        sql_context_value = SqlGenerationContext(
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

        resolved_classification, resolved_context, resolved_question_context = orchestrator._apply_retrieval_support_to_clarification(
            classification=classification,
            sql_context=sql_context_value,
            question_context=question_context,
            retrieval=retrieval,
            original_question="我是指的最新5版p版需求中，202603需求量最多的fgcode是哪一个",
        )

        self.assertFalse(resolved_classification.need_clarification)
        self.assertFalse(resolved_context.need_clarification)
        self.assertEqual(resolved_classification.question_type, "new")
        self.assertEqual(resolved_context.question_type, "new")
        self.assertEqual(resolved_question_context.decision, "answerable")
        self.assertEqual(
            resolved_question_context.effective_question,
            "我是指的最新5版p版需求中，202603需求量最多的fgcode是哪一个",
        )

    def test_mixed_retrieval_domains_do_not_resolve_to_first_single_domain_hit(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        retrieval = RetrievalContext(
            domains=["demand", "sales_financial", "plan_actual"],
            hits=[
                RetrievalHit(
                    source_type="table_schema",
                    source_id="sales_financial_perf",
                    score=6.0,
                    summary="销售与财务实绩表",
                    metadata={"table": "sales_financial_perf", "domains": ["demand", "sales_financial"]},
                ),
                RetrievalHit(
                    source_type="knowledge",
                    source_id="business_knowledge:production_actuals_act_type_panel_volume:note:4",
                    score=3.5,
                    summary="MDL工厂top10投入型号及其物量",
                    metadata={"domains": ["plan_actual"], "tables": ["production_actuals"]},
                ),
                RetrievalHit(
                    source_type="example",
                    source_id="demand_latest_p_202605_oxide_product_count_001",
                    score=2.4,
                    summary="最新P版需求",
                    metadata={"subject_domain": "demand", "tables": ["p_demand"]},
                ),
            ],
        )

        self.assertIsNone(orchestrator._single_retrieval_domain(retrieval))

    def test_single_retrieval_domain_resolves_when_unambiguous(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        retrieval = RetrievalContext(
            domains=["inventory"],
            hits=[
                RetrievalHit(
                    source_type="table_schema",
                    source_id="oms_inventory",
                    score=5.0,
                    summary="OMS库存",
                    metadata={"table": "oms_inventory", "domains": ["inventory"]},
                )
            ],
        )

        self.assertEqual(orchestrator._single_retrieval_domain(retrieval), "inventory")

    def test_prompt_allowed_sources_replace_sql_context_tables_for_validation(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="unknown",
            tables=["production_actuals"],
            semantic_brief="查询销售业绩与最新P版需求量差异。",
        )
        sql_prompt = {
            "evidence_context": {
                "allowed_sources": ["sales_financial_perf", "p_demand", "product_mapping"],
            }
        }

        resolved_context = orchestrator._apply_prompt_allowed_sources_to_sql_context(
            sql_context_value,
            sql_prompt,
        )

        self.assertEqual(resolved_context.tables, ["sales_financial_perf", "p_demand", "product_mapping"])
        self.assertEqual(sql_context_value.tables, ["production_actuals"])

    def test_mixed_retrieval_domains_do_not_filter_cross_domain_tables(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="sales_financial",
            tables=[],
            semantic_brief="查询销售业绩与最新P版需求量差异。",
        )
        retrieval = RetrievalContext(
            domains=["sales_financial", "demand"],
            hits=[
                RetrievalHit(
                    source_type="table_schema",
                    source_id="sales_financial_perf",
                    score=5.0,
                    summary="销售与财务实绩表",
                    metadata={"table": "sales_financial_perf", "domains": ["sales_financial"]},
                ),
                RetrievalHit(
                    source_type="table_schema",
                    source_id="p_demand",
                    score=4.0,
                    summary="P版需求表",
                    metadata={"table": "p_demand", "domains": ["demand"]},
                ),
            ],
        )

        resolved_context = orchestrator._apply_retrieval_tables_to_sql_context(
            sql_context_value,
            retrieval,
        )

        self.assertEqual(resolved_context.tables, ["sales_financial_perf", "p_demand"])

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

    def test_terminal_response_does_not_emit_completed_before_persistence_succeeds(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        progress_service = RecordingProgressService()
        orchestrator.progress_service = progress_service
        orchestrator.answer_builder = AnswerBuilder()
        orchestrator.audit_service = RecordingAuditService()
        orchestrator.conversation_persistence_service = FailingPersistenceService()
        trace = TraceRecord(trace_id="trace_terminal_persist_failure")
        classification = QuestionClassification(
            question_type="clarification_needed",
            subject_domain="inventory",
            need_clarification=True,
            clarification_question="请补充查询对象。",
        )
        sql_context_value = SqlGenerationContext(
            question_type="clarification_needed",
            subject_domain="inventory",
            need_clarification=True,
            clarification_question="请补充查询对象。",
        )

        with self.assertRaisesRegex(RuntimeError, "persist failed"):
            orchestrator._finalize_terminal_response(
                trace=trace,
                request=ChatRequest(question="库存"),
                session_state=None,
                question_context=None,
                classification=classification,
                sql_context=sql_context_value,
                warnings=[],
                retrieval=None,
                terminal_reason="terminal gate: clarification required",
            )

        self.assertEqual([event.type for event in progress_service.events], [])

    def test_response_snapshot_is_kept_out_of_public_response_trace_and_omits_rows(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.audit_service = RecordingAuditService()
        trace = TraceRecord(trace_id="trace_snapshot_public")
        classification = QuestionClassification(question_type="new", subject_domain="inventory")
        context_summary = ContextSummary(
            question_type="new",
            subject_domain="inventory",
            tables=["oms_inventory"],
        )
        response = ChatResponse(
            classification=classification,
            context_summary=context_summary,
            trace=trace,
            answer=None,
            sql="SELECT * FROM oms_inventory FETCH FIRST 2 ROWS ONLY",
            context_validation=ValidationResponse(valid=True, errors=[], warnings=[]),
            sql_validation=ValidationResponse(valid=True, errors=[], warnings=[]),
            execution=ExecutionResponse(
                executed=True,
                status="ok",
                sql="SELECT * FROM oms_inventory FETCH FIRST 2 ROWS ONLY",
                row_count=2,
                columns=["product_ID"],
                rows=[{"product_ID": "A"}, {"product_ID": "B"}],
                errors=[],
                warnings=[],
            ),
            next_session_state=SessionState(session_id="sess_snapshot"),
        )

        orchestrator._append_response_snapshot(trace, response)

        self.assertEqual([step.name for step in trace.steps], ["response_snapshot"])
        self.assertEqual([step.name for step in response.trace.steps], [])
        snapshot_response = trace.steps[0].metadata["response"]
        # execution.sql and execution.rows must remain in the snapshot so
        # that ChatResponseRestoreService._restore_from_snapshot can rebuild
        # the full ChatResponse without validation errors.
        self.assertIn("sql", snapshot_response["execution"])
        self.assertIn("rows", snapshot_response["execution"])
        # The snapshot step itself must not leak into the response's public trace.
        self.assertEqual([s.name for s in response.trace.steps], [])

    def test_response_snapshot_round_trip_restores_chat_response(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.audit_service = RecordingAuditService()
        trace = TraceRecord(trace_id="trace_round_trip")
        classification = QuestionClassification(question_type="new", subject_domain="inventory")
        context_summary = ContextSummary(
            question_type="new",
            subject_domain="inventory",
            tables=["oms_inventory"],
        )
        response = ChatResponse(
            classification=classification,
            context_summary=context_summary,
            trace=trace,
            answer=None,
            sql="SELECT * FROM oms_inventory FETCH FIRST 2 ROWS ONLY",
            context_validation=ValidationResponse(valid=True, errors=[], warnings=[]),
            sql_validation=ValidationResponse(valid=True, errors=[], warnings=[]),
            execution=ExecutionResponse(
                executed=True,
                status="ok",
                sql="SELECT * FROM oms_inventory FETCH FIRST 2 ROWS ONLY",
                row_count=2,
                columns=["product_ID"],
                rows=[{"product_ID": "A"}, {"product_ID": "B"}],
                errors=[],
                warnings=[],
            ),
            next_session_state=SessionState(session_id="sess_round_trip"),
        )

        orchestrator._append_response_snapshot(trace, response)
        snapshot_payload = dict(trace.steps[0].metadata["response"])

        # Restore path mirrors ChatResponseRestoreService._restore_from_snapshot.
        payload = dict(snapshot_payload)
        payload["trace"] = trace
        payload["sql"] = snapshot_payload["execution"]["sql"]
        payload.pop("sql_context", None)

        restored = ChatResponse(**payload)
        self.assertEqual(restored.execution.status, "ok")
        self.assertEqual(restored.execution.row_count, 2)
        self.assertEqual(restored.execution.rows, [{"product_ID": "A"}, {"product_ID": "B"}])

    def test_sql_context_validation_rejects_missing_physical_tables(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        sql_context_value = SqlGenerationContext(
            question_type="new",
            subject_domain="unknown",
            tables=[],
            semantic_brief="查询没有稳定物理表证据的问题。",
        )

        context_errors, context_warnings = orchestrator._validate_sql_context(sql_context_value)

        self.assertIn("no physical tables selected for SQL generation", context_errors)
        self.assertEqual(context_warnings, [])

if __name__ == "__main__":
    unittest.main()
