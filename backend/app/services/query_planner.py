from __future__ import annotations

import logging
import time
from typing import Any

from backend.app.core.cancellation import CancellationToken
from backend.app.models.classification import QuestionClassification, QueryIntent
from backend.app.models.query_plan import FilterItem, QueryPlan, SortItem
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_context_service import QuestionContextService
from backend.app.services.query_intent_parser import QueryIntentParser
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.semantic_bundle_service import SemanticBundleService


logger = logging.getLogger(__name__)


class QueryPlanner:
    def __init__(
        self,
        domain_config: dict[str, Any],
        llm_client: LLMClient,
        prompt_builder: PromptBuilder,
        semantic_runtime: SemanticRuntime | None = None,
        semantic_bundle_service: SemanticBundleService | None = None,
        question_context_service: QuestionContextService | None = None,
        enable_chitchat_mode: bool = False,
    ) -> None:
        _ = enable_chitchat_mode
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime or SemanticRuntime(domain_config)
        self.parser = QueryIntentParser(domain_config, semantic_runtime=self.semantic_runtime)
        self.semantic_bundle_service = semantic_bundle_service or SemanticBundleService(llm_client, prompt_builder)
        self.question_context_service = question_context_service

    def build_planning_trace(
        self,
        question: str,
        session_state: SessionState | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        stage_started = time.perf_counter()
        initial_parser_query_intent = self.parser.parse(question=question, session_state=None)
        initial_parser_signals = self._parser_signals(initial_parser_query_intent)
        self._log_timing("planner.parse_intent", stage_started)

        stage_started = time.perf_counter()
        semantic_bundle = self._build_semantic_bundle(
            original_question=question,
            effective_question=question,
            session_state=session_state,
            parser_signals=initial_parser_signals,
            cancellation_token=cancellation_token,
        )
        effective_question = semantic_bundle.effective_question or question
        self._log_timing("planner.semantic_bundle", stage_started)

        stage_started = time.perf_counter()
        parser_query_intent = self.parser.parse(question=effective_question, session_state=None)
        self._log_timing("planner.parse_effective_intent", stage_started)

        stage_started = time.perf_counter()
        query_intent, contract_compilation = self._compile_query_intent(
            question=effective_question,
            parser_query_intent=parser_query_intent,
            semantic_bundle=semantic_bundle,
        )
        self._log_timing("planner.compile_contract", stage_started)

        stage_started = time.perf_counter()
        classification = self._classification_from_semantic_bundle(
            semantic_bundle=semantic_bundle,
            query_intent=query_intent,
        )
        self._log_timing("planner.classify", stage_started)
        warnings: list[str] = []
        if classification.need_clarification:
            warnings.append("clarification required before stable SQL generation")
        self._log_timing("planner.total", total_started)
        return {
            "original_question": question,
            "effective_question": effective_question,
            "semantic_bundle": semantic_bundle,
            "query_intent": query_intent,
            "contract_compilation": contract_compilation,
            "classification": classification,
            "warnings": warnings,
        }

    def _parser_signals(self, query_intent: QueryIntent) -> dict[str, Any]:
        return {
            "matched_metrics": list(query_intent.matched_metrics),
            "matched_entities": list(query_intent.matched_entities),
            "requested_dimensions": list(query_intent.requested_dimensions),
            "filters": [item.model_dump(mode="json") for item in query_intent.filters],
            "filter_fields": [item.field for item in query_intent.filters],
            "time_context": query_intent.time_context.model_dump(mode="json"),
            "time_grain": query_intent.time_context.grain,
            "version_context": query_intent.version_context.model_dump(mode="json") if query_intent.version_context else None,
            "has_version_context": query_intent.version_context is not None,
            "requested_limit": query_intent.requested_limit,
            "sort": [item.model_dump(mode="json") for item in query_intent.requested_sort],
            "sort_fields": [item.field for item in query_intent.requested_sort],
            "analysis_mode": query_intent.analysis_mode,
            "subject_domain": query_intent.subject_domain,
            "has_follow_up_cue": query_intent.has_follow_up_cue,
            "has_explicit_slots": query_intent.has_explicit_slots,
        }

    def _build_semantic_bundle(
        self,
        *,
        original_question: str,
        effective_question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any],
        cancellation_token: CancellationToken | None,
    ):
        if self.question_context_service is not None:
            return self.question_context_service.build(
                question=original_question,
                session_state=session_state,
                parser_signals=parser_signals,
                cancellation_token=cancellation_token,
            )
        if self.semantic_bundle_service is None:
            from backend.app.models.semantic_bundle import SemanticBundle

            return SemanticBundle(
                original_question=original_question,
                effective_question=effective_question,
                subject_domain=str(parser_signals.get("subject_domain") or "unknown"),
                user_intent=effective_question,
                semantic_brief=effective_question,
                contract_hint={},
                source="fallback",
                reason="semantic_bundle_service_not_configured",
            )
        return self.semantic_bundle_service.build(
            original_question=original_question,
            effective_question=effective_question,
            session_state=session_state,
            parser_signals=parser_signals,
            cancellation_token=cancellation_token,
        )

    def _log_timing(self, stage: str, started_at: float) -> None:
        logger.info("timing stage=%s elapsed_ms=%s", stage, int((time.perf_counter() - started_at) * 1000))

    def _classification_from_semantic_bundle(
        self,
        *,
        semantic_bundle,
        query_intent: QueryIntent,
    ) -> QuestionClassification:
        decision = getattr(semantic_bundle, "decision", "answerable")
        if decision == "answerable":
            return QuestionClassification(
                question_type="new",
                subject_domain=self._bundle_domain(semantic_bundle, query_intent),
                inherit_context=False,
                confidence=0.9,
                reason=getattr(semantic_bundle, "reason", None) or getattr(semantic_bundle, "semantic_brief", None),
                reason_code="semantic_bundle_answerable",
                need_clarification=False,
            )
        if decision == "clarification_needed":
            return QuestionClassification(
                question_type="clarification_needed",
                subject_domain=self._bundle_domain(semantic_bundle, query_intent),
                inherit_context=False,
                confidence=0.9,
                reason=getattr(semantic_bundle, "reason", None),
                reason_code="semantic_bundle_clarification",
                need_clarification=True,
                clarification_question=getattr(semantic_bundle, "clarification_question", None) or "请补充查询对象、指标或时间范围。",
            )
        if decision == "invalid":
            return QuestionClassification(
                question_type="invalid",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.9,
                reason=getattr(semantic_bundle, "reason", None) or "语义包判断当前问题不属于可执行业务查询。",
                reason_code="semantic_bundle_invalid",
                need_clarification=False,
            )
        return QuestionClassification(
            question_type="clarification_needed",
            subject_domain=self._bundle_domain(semantic_bundle, query_intent),
            inherit_context=False,
            confidence=0.5,
            reason="semantic bundle decision is unknown",
            reason_code="semantic_bundle_unknown_decision",
            need_clarification=True,
            clarification_question="请补充查询对象、指标或时间范围。",
        )

    def _bundle_domain(self, semantic_bundle, query_intent: QueryIntent) -> str:
        subject_domain = str(getattr(semantic_bundle, "subject_domain", "unknown") or "unknown")
        if self.semantic_runtime.is_known_domain(subject_domain):
            return subject_domain
        return query_intent.subject_domain

    def _compile_query_intent(
        self,
        *,
        question: str,
        parser_query_intent: QueryIntent,
        semantic_bundle,
    ) -> tuple[QueryIntent, dict[str, Any]]:
        return parser_query_intent, {
            "status": "skipped",
            "source": "question_context_text_only",
            "intent": None,
            "warnings": ["structured intent compilation skipped; text context drives retrieval and SQL generation"],
            "raw_payload": {
                "effective_question": question,
                "semantic_brief": getattr(semantic_bundle, "semantic_brief", None),
                "context_relation": getattr(semantic_bundle, "context_relation", None),
                "decision": getattr(semantic_bundle, "decision", None),
            },
            "parser_observations": self._parser_signals(parser_query_intent),
        }

    def _structured_intent_payload_from_bundle(self, semantic_bundle) -> dict[str, Any]:
        contract_hint = self._bundle_contract_hint(semantic_bundle)
        subject_domain = str(contract_hint.get("subject_domain") or getattr(semantic_bundle, "subject_domain", "unknown") or "unknown")
        return {
            "subject_domain": subject_domain,
            "metrics": self._hint_string_list(contract_hint, "metrics"),
            "entities": self._hint_string_list(contract_hint, "entities"),
            "dimensions": self._hint_string_list(contract_hint, "dimensions"),
            "filters": [item.model_dump(mode="json") for item in self._hint_filters(contract_hint)],
            "time_context": contract_hint.get("time_context"),
            "version_context": contract_hint.get("version_context"),
            "analysis_mode": contract_hint.get("analysis_mode"),
            "confidence": 0.9,
            "reason": getattr(semantic_bundle, "semantic_brief", None),
        }

    def classify(
        self,
        question: str,
        session_state: SessionState | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> tuple[QueryIntent, QuestionClassification, list[str]]:
        planning_trace = self.build_planning_trace(
            question=question,
            session_state=session_state,
            cancellation_token=cancellation_token,
        )
        return (
            planning_trace["query_intent"],
            planning_trace["classification"],
            planning_trace["warnings"],
        )

    def create_plan(
        self,
        question: str,
        session_state: SessionState | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> tuple[QueryIntent, QuestionClassification, QueryPlan, list[str]]:
        planning_trace = self.build_planning_trace(
            question=question,
            session_state=session_state,
            cancellation_token=cancellation_token,
        )
        query_intent = planning_trace["query_intent"]
        classification = planning_trace["classification"]
        warnings = planning_trace["warnings"]

        query_plan = self.build_plan_from_intent(
            classification=classification,
            query_intent=query_intent,
            session_state=session_state,
            semantic_bundle=planning_trace.get("semantic_bundle"),
        )
        return query_intent, classification, query_plan, warnings

    def build_plan_from_intent(
        self,
        *,
        classification: QuestionClassification,
        query_intent: QueryIntent | None = None,
        session_state: SessionState | None = None,
        semantic_bundle=None,
        matched_metrics: list[str] | None = None,
        matched_entities: list[str] | None = None,
        filters: list | None = None,
        time_context=None,
        version_context=None,
        analysis_mode: str | None = None,
        sort: list[SortItem] | None = None,
        limit: int | None = None,
        requested_dimensions: list[str] | None = None,
    ) -> QueryPlan:
        if query_intent is not None:
            matched_entities = list(query_intent.matched_entities)
            matched_metrics = []
            filters = []
            time_context = query_intent.time_context
            version_context = query_intent.version_context
            analysis_mode = query_intent.analysis_mode
            sort = list(query_intent.requested_sort)
            limit = query_intent.requested_limit or self.semantic_runtime.default_limit(classification.subject_domain)
            requested_dimensions = []

        matched_metrics = list(matched_metrics or [])
        matched_entities = list(matched_entities or [])
        filters = list(filters or [])
        requested_dimensions = list(requested_dimensions or [])
        sort = list(sort or [])
        if limit is None:
            limit = self.semantic_runtime.default_limit(classification.subject_domain)

        subject_domain = classification.subject_domain
        query_plan = QueryPlan(
            subject_domain=subject_domain,
            question_type=classification.question_type,
            metrics=[],
            dimensions=[],
            filters=[],
            tables=[],
            time_context=time_context,
            version_context=version_context,
            inherit_context=classification.inherit_context,
            analysis_mode=analysis_mode,
            sort=sort,
            limit=limit,
            entities=[],
            need_clarification=classification.need_clarification,
            clarification_question=classification.clarification_question,
            calculation_contract={},
        )
        sanitized = query_plan
        bundle_brief = getattr(semantic_bundle, "semantic_brief", None)
        sanitized.semantic_brief = bundle_brief or self._build_semantic_brief(sanitized)
        return sanitized

    def _has_calculation_contract(self, contract_hint: dict[str, Any]) -> bool:
        value = contract_hint.get("calculation_contract")
        return isinstance(value, dict) and bool(value)

    def _calculation_contract_tables(self, calculation_contract: dict[str, Any]) -> list[str]:
        tables: list[str] = []
        for source in calculation_contract.get("sources", []) if isinstance(calculation_contract, dict) else []:
            if not isinstance(source, dict):
                continue
            table_name = str(source.get("table") or source.get("source") or "").strip()
            if table_name and self.semantic_runtime.is_known_table(table_name) and table_name not in tables:
                tables.append(table_name)
        return tables

    def _build_semantic_brief(self, query_plan: QueryPlan) -> str:
        parts: list[str] = []
        domain_label = self._domain_label(query_plan.subject_domain)
        if query_plan.metrics:
            parts.append("查询" + "、".join(query_plan.metrics))
        else:
            parts.append("查询业务数据")
        if domain_label:
            parts.append(f"业务域是{domain_label}")
        if query_plan.dimensions:
            parts.append("按" + "、".join(query_plan.dimensions) + "展示")
        filter_brief = self._filter_brief(query_plan.filters)
        if filter_brief:
            parts.append("条件为" + filter_brief)
        if query_plan.time_context and query_plan.time_context.range:
            time_range = query_plan.time_context.range
            if time_range.start or time_range.end:
                parts.append(f"时间范围{time_range.start or '?'}至{time_range.end or '?'}")
        if query_plan.version_context and query_plan.version_context.value:
            parts.append(f"版本为{query_plan.version_context.value}")
        if query_plan.sort:
            sort_text = "、".join(f"{item.field}{item.order}" for item in query_plan.sort)
            parts.append("排序" + sort_text)
        if query_plan.limit:
            parts.append(f"最多返回{query_plan.limit}行")
        return "；".join(parts) + "。"

    def _filter_brief(self, filters: list[FilterItem]) -> str:
        if not filters:
            return ""
        values = []
        for item in filters[:10]:
            if item.op == "between" and isinstance(item.value, list) and len(item.value) >= 2:
                values.append(f"{item.field}在{item.value[0]}到{item.value[1]}之间")
            elif item.op == "latest_n" and isinstance(item.value, dict):
                values.append(f"{item.field}取最新{item.value.get('count', 1)}个")
            elif item.op in {"is_null", "not_null"}:
                values.append(f"{item.field}{item.op}")
            else:
                values.append(f"{item.field}{item.op}{item.value}")
        return "、".join(values)

    def _domain_label(self, subject_domain: str) -> str:
        labels = {
            "inventory": "库存",
            "demand": "需求",
            "plan_actual": "计划实际",
            "sales_financial": "销售财务",
            "dimension": "维表",
            "unknown": "",
        }
        return labels.get(subject_domain, subject_domain)

    def _bundle_contract_hint(self, semantic_bundle) -> dict[str, Any]:
        if semantic_bundle is None:
            return {}
        contract_hint = getattr(semantic_bundle, "contract_hint", {})
        return contract_hint if isinstance(contract_hint, dict) else {}

    def _hint_string_list(self, contract_hint: dict[str, Any], key: str) -> list[str]:
        value = contract_hint.get(key)
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _hint_tables(self, contract_hint: dict[str, Any]) -> list[str]:
        return [
            table_name
            for table_name in self._hint_string_list(contract_hint, "tables")
            if self.semantic_runtime.is_known_table(table_name)
        ]

    def _hint_filters(self, contract_hint: dict[str, Any]) -> list[FilterItem]:
        value = contract_hint.get("filters")
        if not isinstance(value, list):
            return []
        filters: list[FilterItem] = []
        for item in value:
            if isinstance(item, FilterItem):
                filters.append(item)
                continue
            if isinstance(item, dict):
                try:
                    filters.append(FilterItem.model_validate(item))
                except Exception:
                    continue
        return filters

    def _hint_time_context(self, contract_hint: dict[str, Any]):
        value = contract_hint.get("time_context")
        if not isinstance(value, dict):
            return None
        try:
            from backend.app.models.query_plan import TimeContext

            return TimeContext.model_validate(value)
        except Exception:
            return None

    def _hint_version_context(self, contract_hint: dict[str, Any]):
        value = contract_hint.get("version_context")
        if not isinstance(value, dict):
            return None
        try:
            from backend.app.models.query_plan import VersionContext

            return VersionContext.model_validate(value)
        except Exception:
            return None
