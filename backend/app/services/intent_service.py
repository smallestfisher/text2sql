from __future__ import annotations

from backend.app.models.classification import QueryIntent
from backend.app.models.intent import StructuredIntent
from backend.app.models.session_state import SessionState
from backend.app.core.cancellation import CancellationToken
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder


class IntentService:
    def __init__(
        self,
        llm_client: LLMClient,
        prompt_builder: PromptBuilder,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder

    def generate_intent(
        self,
        *,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> dict:
        if self._can_use_parser_intent(query_intent=query_intent, session_state=session_state):
            intent = StructuredIntent.from_query_intent(query_intent)
            raw = {
                "mode": "parser_shortcut",
                "reason": "parser_intent_has_known_domain_metric_and_explicit_slots",
            }
            return {
                "status": "skipped",
                "reason": raw["reason"],
                "intent": intent,
                "raw": raw,
            }

        prompt_payload = self.prompt_builder.build_intent_prompt(
            question=question,
            query_intent=query_intent,
            session_state=session_state,
        )
        hint = self.llm_client.generate_intent(
            prompt_payload,
            cancellation_token=cancellation_token,
        )
        intent = StructuredIntent.from_llm_payload(
            normalized_question=query_intent.normalized_question,
            payload=hint,
        )
        return {
            "status": "completed",
            "reason": None,
            "intent": intent,
            "raw": hint,
        }

    def _can_use_parser_intent(
        self,
        *,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> bool:
        if session_state is not None:
            return False
        if query_intent.has_follow_up_cue:
            return False
        if query_intent.subject_domain == "unknown":
            return False
        if not query_intent.matched_metrics:
            return False
        if not query_intent.has_explicit_slots:
            return False
        return True
