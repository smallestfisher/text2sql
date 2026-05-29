from __future__ import annotations

import logging
from typing import Any

from backend.app.core.cancellation import CancellationToken
from backend.app.core.exceptions import LLMServiceError
from backend.app.models.question_context import QuestionContext
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


class SemanticBundleService:
    def __init__(self, llm_client: LLMClient, prompt_builder: PromptBuilder) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder

    def build(
        self,
        *,
        original_question: str,
        effective_question: str | None = None,
        session_state: SessionState | None = None,
        parser_signals: dict[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> QuestionContext:
        fallback = self._fallback_bundle(
            original_question=original_question,
            effective_question=effective_question or original_question,
            parser_signals=parser_signals or {},
            reason="llm_unavailable",
        )
        if not getattr(self.llm_client, "enabled", False) or not hasattr(self.llm_client, "generate_question_context"):
            return fallback

        prompt_payload = self.prompt_builder.build_question_context_prompt(
            question=effective_question or original_question,
            session_state=session_state,
            parser_signals=parser_signals or {},
        )
        try:
            payload = self.llm_client.generate_question_context(
                prompt_payload,
                cancellation_token=cancellation_token,
            )
        except LLMServiceError as exc:
            logger.warning("semantic bundle generation failed; using fallback bundle: %s", exc)
            return fallback.model_copy(update={"reason": str(exc)}, deep=True)
        return self._coerce_payload(
            payload,
            original_question=original_question,
            effective_question=effective_question or original_question,
            parser_signals=parser_signals or {},
        )

    def _coerce_payload(
        self,
        payload: Any,
        *,
        original_question: str,
        effective_question: str,
        parser_signals: dict[str, Any],
    ) -> QuestionContext:
        if not isinstance(payload, dict):
            return self._fallback_bundle(
                original_question=original_question,
                effective_question=effective_question,
                parser_signals=parser_signals,
                reason="semantic bundle payload is not an object",
            )
        decision = str(payload.get("decision") or "answerable").strip()
        if decision not in {"answerable", "clarification_needed", "invalid"}:
            decision = "answerable"
        rewritten_question = str(payload.get("effective_question") or payload.get("rewritten_question") or effective_question).strip()
        context_relation = str(payload.get("context_relation") or payload.get("context_decision") or payload.get("rewrite_decision") or "new").strip()
        if context_relation == "clarification_needed":
            context_relation = "ambiguous"
        if context_relation not in {"follow_up", "new", "ambiguous"}:
            context_relation = "new"
        semantic_brief = str(payload.get("semantic_brief") or "").strip()
        user_intent = str(payload.get("user_intent") or "").strip()
        if not semantic_brief:
            semantic_brief = user_intent or effective_question
        return QuestionContext(
            original_question=original_question,
            effective_question=rewritten_question or effective_question,
            context_relation=context_relation,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            conversation_summary="",
            semantic_brief=semantic_brief,
            clarification_question=self._optional_string(payload.get("clarification_question")),
            reason=self._optional_string(payload.get("reason")),
            source="llm",
            raw_payload=payload,
            subject_domain=str(payload.get("subject_domain") or parser_signals.get("subject_domain") or "unknown").strip(),
        )

    def _fallback_bundle(
        self,
        *,
        original_question: str,
        effective_question: str,
        parser_signals: dict[str, Any],
        reason: str,
    ) -> QuestionContext:
        subject_domain = str(parser_signals.get("subject_domain") or "unknown")
        metrics = parser_signals.get("matched_metrics") or []
        dimensions = parser_signals.get("requested_dimensions") or []
        filters = parser_signals.get("filter_fields") or []
        brief_parts = [f"用户问题：{effective_question}"]
        if subject_domain != "unknown":
            brief_parts.append(f"业务域：{subject_domain}")
        if metrics:
            brief_parts.append("指标：" + "、".join(str(item) for item in metrics))
        if dimensions:
            brief_parts.append("展示维度：" + "、".join(str(item) for item in dimensions))
        if filters:
            brief_parts.append("过滤条件涉及：" + "、".join(str(item) for item in filters))
        return QuestionContext(
            original_question=original_question,
            effective_question=effective_question,
            context_relation="new",
            decision="answerable",
            semantic_brief="；".join(brief_parts) + "。",
            reason=reason,
            source="fallback",
            subject_domain=subject_domain,
        )

    def _optional_string(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
