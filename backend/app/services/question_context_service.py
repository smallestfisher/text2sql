from __future__ import annotations

from typing import Any

from backend.app.core.cancellation import CancellationToken
from backend.app.core.exceptions import LLMServiceError
from backend.app.models.question_context import QuestionContext
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder


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
        question_context, _prompt_metadata = self.build_with_prompt_metadata(
            question=question,
            session_state=session_state,
            parser_signals=parser_signals,
            cancellation_token=cancellation_token,
        )
        return question_context

    def build_with_prompt_metadata(
        self,
        *,
        question: str,
        session_state: SessionState | None = None,
        parser_signals: dict[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> tuple[QuestionContext, dict[str, Any]]:
        if not getattr(self.llm_client, "enabled", False) or not hasattr(self.llm_client, "generate_question_context"):
            raise LLMServiceError("question context generation requires an enabled LLM client")

        prompt_payload = self.prompt_builder.build_question_context_prompt(
            question=question,
            session_state=session_state,
            parser_signals=parser_signals or {},
        )
        payload = self.llm_client.generate_question_context(
            prompt_payload,
            cancellation_token=cancellation_token,
        )
        question_context = self._coerce_payload(
            payload,
            question=question,
            session_state=session_state,
            parser_signals=parser_signals or {},
        )
        return question_context, {
            "prompt_diagnostics": prompt_payload.get("prompt_diagnostics", {}),
        }

    def _coerce_payload(
        self,
        payload: Any,
        *,
        question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any],
    ) -> QuestionContext:
        if not isinstance(payload, dict):
            raise LLMServiceError("question context payload is not an object")

        decision = str(payload.get("decision") or "answerable").strip()
        if decision not in {"answerable", "clarification_needed", "invalid"}:
            raise LLMServiceError(f"unsupported question context decision: {decision}")

        context_relation = str(payload.get("context_relation") or "new").strip()
        if context_relation not in {"new", "follow_up", "ambiguous"}:
            raise LLMServiceError(f"unsupported question context relation: {context_relation}")

        effective_question = str(
            payload.get("effective_question")
            or ("" if decision == "clarification_needed" else question)
        ).strip()
        semantic_brief = str(payload.get("semantic_brief") or "").strip()
        if not semantic_brief and effective_question:
            semantic_brief = effective_question

        clarification_question = self._optional_string(payload.get("clarification_question"))
        reason = self._optional_string(payload.get("reason"))

        return QuestionContext(
            original_question=question,
            effective_question=effective_question,
            context_relation=context_relation,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            conversation_summary=self.prompt_builder.conversation_summary(session_state) if session_state is not None else "",
            semantic_brief=semantic_brief,
            clarification_question=clarification_question,
            reason=reason,
            source="llm",
            raw_payload=payload,
            subject_domain=str(payload.get("subject_domain") or parser_signals.get("subject_domain") or "unknown"),
        )

    def _optional_string(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
