from __future__ import annotations

from backend.app.models.classification import QueryIntent
from backend.app.models.intent import StructuredIntent
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder


class IntentService:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder

    def generate_shadow_intent(
        self,
        *,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None = None,
    ) -> dict:
        if self.llm_client is None or self.prompt_builder is None:
            return {
                "status": "skipped",
                "reason": "intent shadow dependencies unavailable",
                "intent": None,
                "raw": None,
            }

        prompt_payload = self.prompt_builder.build_intent_prompt(
            question=question,
            query_intent=query_intent,
            session_state=session_state,
        )
        hint = self.llm_client.generate_intent(prompt_payload)
        if hint.get("mode") != "live":
            return {
                "status": "stub",
                "reason": hint.get("note") or "intent shadow llm unavailable",
                "intent": None,
                "raw": hint,
            }

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
