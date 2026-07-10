from __future__ import annotations

import logging
import time
from typing import Any

from backend.app.core.cancellation import CancellationToken
from backend.app.models.classification import QuestionClassification
from backend.app.models.semantic_types import SUPPORTED_SUBJECT_DOMAINS, TimeContext
from backend.app.models.session_state import SessionState
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_context_service import QuestionContextService
from backend.app.services.semantic_runtime import SemanticRuntime


logger = logging.getLogger(__name__)


class QuestionAnalysisService:
    def __init__(
        self,
        domain_config: dict[str, Any],
        llm_client: LLMClient,
        prompt_builder: PromptBuilder,
        semantic_runtime: SemanticRuntime | None = None,
        question_context_service: QuestionContextService | None = None,
    ) -> None:
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime or SemanticRuntime(domain_config)
        self.question_context_service = question_context_service or QuestionContextService(llm_client, prompt_builder)

    def analyze_question(
        self,
        question: str,
        session_state: SessionState | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        stage_started = time.perf_counter()
        question_context, prompt_metadata = self._build_question_context(
            question=question,
            session_state=session_state,
            cancellation_token=cancellation_token,
        )
        effective_question = question_context.effective_question or question
        self._log_timing("analysis.question_context", stage_started)

        stage_started = time.perf_counter()
        classification = self._classification_from_question_context(
            question_context=question_context,
        )
        self._log_timing("analysis.classify", stage_started)
        warnings: list[str] = []
        if classification.need_clarification:
            warnings.append("clarification required before stable SQL generation")
        self._log_timing("analysis.total", total_started)
        return {
            "original_question": question,
            "effective_question": effective_question,
            "question_context": question_context,
            "classification": classification,
            "warnings": warnings,
            **prompt_metadata,
        }

    def _build_question_context(
        self,
        *,
        question: str,
        session_state: SessionState | None,
        cancellation_token: CancellationToken | None,
    ):
        return self.question_context_service.build_with_prompt_metadata(
            question=question,
            session_state=session_state,
            parser_signals={},
            cancellation_token=cancellation_token,
        )

    def _log_timing(self, stage: str, started_at: float) -> None:
        logger.info("timing stage=%s elapsed_ms=%s", stage, int((time.perf_counter() - started_at) * 1000))

    def _classification_from_question_context(
        self,
        *,
        question_context,
    ) -> QuestionClassification:
        decision = getattr(question_context, "decision", "answerable")
        if decision == "answerable":
            context_relation = getattr(question_context, "context_relation", "new")
            question_type = "follow_up" if context_relation == "follow_up" else "new"
            return QuestionClassification(
                question_type=question_type,
                subject_domain=self._context_domain(question_context),
                inherit_context=False,
                confidence=0.9,
                reason=getattr(question_context, "reason", None) or getattr(question_context, "semantic_brief", None),
                reason_code="question_context_answerable",
                need_clarification=False,
            )
        if decision == "clarification_needed":
            return QuestionClassification(
                question_type="clarification_needed",
                subject_domain=self._context_domain(question_context),
                inherit_context=False,
                confidence=0.9,
                reason=getattr(question_context, "reason", None),
                reason_code="question_context_clarification",
                need_clarification=True,
                clarification_question=getattr(question_context, "clarification_question", None) or "请补充查询对象、指标或时间范围。",
            )
        if decision == "invalid":
            return QuestionClassification(
                question_type="invalid",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.9,
                reason=getattr(question_context, "reason", None) or "问题上下文判断当前问题不属于可执行业务查询。",
                reason_code="question_context_invalid",
                need_clarification=False,
            )
        return QuestionClassification(
            question_type="clarification_needed",
            subject_domain=self._context_domain(question_context),
            inherit_context=False,
            confidence=0.5,
            reason="question context decision is unknown",
            reason_code="question_context_unknown_decision",
            need_clarification=True,
            clarification_question="请补充查询对象、指标或时间范围。",
        )

    def _context_domain(self, question_context) -> str:
        subject_domain = str(getattr(question_context, "subject_domain", "unknown") or "unknown")
        if subject_domain in SUPPORTED_SUBJECT_DOMAINS:
            return subject_domain
        return "unknown"

    def build_sql_context(
        self,
        *,
        classification: QuestionClassification,
        session_state: SessionState | None = None,
        question_context=None,
    ) -> SqlGenerationContext:
        _ = session_state
        subject_domain = classification.subject_domain
        limit = self.semantic_runtime.default_limit(subject_domain)
        sql_context = SqlGenerationContext(
            subject_domain=subject_domain,
            question_type=classification.question_type,
            metrics=[],
            dimensions=[],
            filters=[],
            tables=[],
            time_context=TimeContext(),
            version_context=None,
            inherit_context=classification.inherit_context,
            analysis_mode=None,
            sort=[],
            limit=limit,
            entities=[],
            need_clarification=classification.need_clarification,
            clarification_question=classification.clarification_question,
            reason=classification.reason,
            reason_code=classification.reason_code,
        )
        sql_context.semantic_brief = (
            getattr(question_context, "semantic_brief", None)
            or getattr(question_context, "effective_question", None)
            or "查询业务数据。"
        )
        return sql_context
