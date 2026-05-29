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


class QuestionContextService:
    def __init__(self, llm_client: LLMClient, prompt_builder: PromptBuilder) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder

    def build(
        self,
        *,
        question: str,
        session_state: SessionState | None = None,
        parser_signals: dict[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> QuestionContext:
        fallback = self._fallback_context(
            question=question,
            session_state=session_state,
            parser_signals=parser_signals or {},
            reason="llm_unavailable",
        )
        if not getattr(self.llm_client, "enabled", False) or not hasattr(self.llm_client, "generate_question_context"):
            return fallback

        prompt_payload = self.prompt_builder.build_question_context_prompt(
            question=question,
            session_state=session_state,
            parser_signals=parser_signals or {},
        )
        try:
            payload = self.llm_client.generate_question_context(
                prompt_payload,
                cancellation_token=cancellation_token,
            )
        except LLMServiceError as exc:
            logger.warning("question context generation failed; using fallback context: %s", exc)
            return fallback.model_copy(update={"reason": str(exc)}, deep=True)
        return self._coerce_payload(
            payload,
            question=question,
            session_state=session_state,
            parser_signals=parser_signals or {},
        )

    def _coerce_payload(
        self,
        payload: Any,
        *,
        question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any],
    ) -> QuestionContext:
        if not isinstance(payload, dict):
            return self._fallback_context(
                question=question,
                session_state=session_state,
                parser_signals=parser_signals,
                reason="question context payload is not an object",
            )

        decision = str(payload.get("decision") or "answerable").strip()
        if decision not in {"answerable", "clarification_needed", "invalid"}:
            decision = "answerable"

        context_relation = str(payload.get("context_relation") or payload.get("context_decision") or "new").strip()
        if context_relation == "clarification_needed":
            context_relation = "ambiguous"
        if context_relation not in {"new", "follow_up", "ambiguous"}:
            context_relation = "new"

        effective_question = str(
            payload.get("effective_question")
            or payload.get("rewritten_question")
            or ("" if decision == "clarification_needed" else question)
        ).strip()
        semantic_brief = str(payload.get("semantic_brief") or payload.get("user_intent") or "").strip()
        if not semantic_brief and effective_question:
            semantic_brief = effective_question

        return QuestionContext(
            original_question=question,
            effective_question=effective_question,
            context_relation=context_relation,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            conversation_summary=self.prompt_builder.conversation_brief(session_state) if session_state is not None else "",
            semantic_brief=semantic_brief,
            clarification_question=self._optional_string(payload.get("clarification_question")),
            reason=self._optional_string(payload.get("reason")),
            source="llm",
            raw_payload=payload,
            subject_domain=str(payload.get("subject_domain") or parser_signals.get("subject_domain") or "unknown"),
        )

    def _fallback_context(
        self,
        *,
        question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any],
        reason: str,
    ) -> QuestionContext:
        conversation_summary = self.prompt_builder.conversation_brief(session_state) if session_state is not None else ""
        return QuestionContext(
            original_question=question,
            effective_question=question,
            context_relation="new",
            decision="answerable",
            conversation_summary=conversation_summary,
            semantic_brief=question,
            reason=reason,
            source="fallback",
            subject_domain=str(parser_signals.get("subject_domain") or "unknown"),
        )

    def _optional_string(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
