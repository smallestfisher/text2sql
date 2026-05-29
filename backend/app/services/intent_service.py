from __future__ import annotations

import logging
import re
from typing import Any

from backend.app.models.classification import QueryIntent
from backend.app.models.query_plan import FilterItem
from backend.app.models.intent import StructuredIntent
from backend.app.models.session_state import SessionState
from backend.app.core.cancellation import CancellationToken
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


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
        payload, coercion_warnings = self._coerce_llm_payload(hint)
        try:
            intent = StructuredIntent.from_llm_payload(
                normalized_question=query_intent.normalized_question,
                payload=payload,
            )
        except ValueError as exc:
            logger.warning("llm intent payload rejected; falling back to parser intent: %s", exc)
            return self._fallback_to_parser_intent(
                query_intent=query_intent,
                session_state=session_state,
                raw_payload=hint,
                reason=str(exc),
                coercion_warnings=coercion_warnings,
            )
        if coercion_warnings:
            intent.raw_payload = {
                **intent.raw_payload,
                "coercion_warnings": coercion_warnings,
                "llm_payload": hint,
            }
        return {
            "status": "completed",
            "reason": None,
            "intent": intent,
            "raw": hint,
        }

    def _fallback_to_parser_intent(
        self,
        *,
        query_intent: QueryIntent,
        session_state: SessionState | None,
        raw_payload: Any,
        reason: str,
        coercion_warnings: list[str],
    ) -> dict:
        intent = StructuredIntent.from_query_intent(query_intent)
        if (
            intent.subject_domain == "unknown"
            and session_state is not None
            and query_intent.has_explicit_slots
        ):
            intent.subject_domain = session_state.subject_domain
        intent.raw_payload = {
            **intent.raw_payload,
            "fallback_reason": "llm_intent_payload_invalid",
            "llm_error": reason,
            "coercion_warnings": coercion_warnings,
            "llm_payload": raw_payload,
        }
        return {
            "status": "fallback",
            "reason": f"llm_intent_payload_invalid: {reason}",
            "intent": intent,
            "raw": raw_payload,
        }

    def _coerce_llm_payload(self, payload: Any) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(payload, dict):
            return {}, ["intent payload is not a JSON object"]

        coerced = dict(payload)
        warnings: list[str] = []
        filters = coerced.get("filters")
        if filters is None:
            return coerced, warnings
        if not isinstance(filters, list):
            coerced["filters"] = []
            warnings.append("drop filters: filters is not a JSON array")
            return coerced, warnings

        parsed_filters: list[dict[str, Any]] = []
        for index, item in enumerate(filters):
            if isinstance(item, dict):
                parsed_filters.append(item)
                continue
            if isinstance(item, str):
                parsed = self._coerce_filter_string(item)
                if parsed is not None:
                    parsed_filters.append(parsed.model_dump(mode="json"))
                    warnings.append(f"coerce filters[{index}] from string")
                    continue
            warnings.append(f"drop filters[{index}]: unsupported filter shape")
        coerced["filters"] = parsed_filters
        return coerced, warnings

    def _coerce_filter_string(self, value: str) -> FilterItem | None:
        raw = value.strip()
        if not raw:
            return None

        field_only = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)", raw)
        if field_only:
            field = field_only.group(1)
            if field.upper().startswith("IS_") and self._known_filter_field(field):
                return FilterItem(field=field, op="=", value="Y")
            return None

        match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=|>=|<=|>|<|like|LIKE)\s*['\"]?([^'\"]+)['\"]?",
            raw,
        )
        if not match:
            return None
        field = match.group(1)
        if not self._known_filter_field(field):
            return None
        op = match.group(2).lower()
        parsed_value = match.group(3).strip()
        return FilterItem(field=field, op=op, value=parsed_value)

    def _known_filter_field(self, field_name: str) -> bool:
        semantic_runtime = getattr(self.prompt_builder, "semantic_runtime", None)
        if semantic_runtime is None:
            return True
        for domain_name in getattr(semantic_runtime, "query_profiles", {}).keys():
            if field_name in semantic_runtime.profile_allowed_fields(domain_name):
                return True
        for table_name in getattr(semantic_runtime, "table_field_catalog", {}).keys():
            if field_name in semantic_runtime.table_fields(table_name):
                return True
        return False

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
