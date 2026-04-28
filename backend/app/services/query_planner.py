from __future__ import annotations

from typing import Any

from backend.app.models.classification import QuestionClassification, QueryIntent
from backend.app.models.intent import StructuredIntent
from backend.app.models.query_plan import QueryPlan
from backend.app.models.query_plan import SortItem
from backend.app.models.session_state import SessionState
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.intent_service import IntentService
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_classifier import QuestionClassifier
from backend.app.services.query_intent_parser import QueryIntentParser
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryPlanner:
    def __init__(
        self,
        domain_config: dict[str, Any],
        semantic_runtime: SemanticRuntime | None = None,
        llm_client: LLMClient | None = None,
        prompt_builder: PromptBuilder | None = None,
        intent_service: IntentService | None = None,
        intent_normalizer: IntentNormalizer | None = None,
    ) -> None:
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime or SemanticRuntime(domain_config)
        self.parser = QueryIntentParser(domain_config, semantic_runtime=self.semantic_runtime)
        self.classifier = QuestionClassifier(
            semantic_runtime=self.semantic_runtime,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
        )
        self.intent_service = intent_service
        self.intent_normalizer = intent_normalizer

    def build_planning_trace(
        self,
        question: str,
        session_state: SessionState | None = None,
    ) -> dict[str, Any]:
        parser_query_intent = self.parser.parse(question=question, session_state=session_state)
        parser_intent = StructuredIntent.from_query_intent(parser_query_intent)
        llm_intent = self._build_llm_intent(
            question=question,
            query_intent=parser_query_intent,
            session_state=session_state,
        )
        normalized_intent = self._normalize_intent(llm_intent)
        query_intent, intent_selection = self._select_effective_query_intent(
            parser_query_intent=parser_query_intent,
            normalized_intent_payload=normalized_intent,
        )
        classification, classifier_warnings = self.classifier.classify(
            question=question,
            query_intent=query_intent,
            session_state=session_state,
        )
        warnings: list[str] = list(classifier_warnings)
        if classification.need_clarification:
            warnings.append("clarification required before stable SQL generation")
        semantic_diff = (
            self.semantic_runtime.session_semantic_diff(query_intent, session_state)
            if session_state is not None
            else {}
        )
        classifier_debug = self.classifier.last_debug_info()
        return {
            "query_intent": query_intent,
            "parser_query_intent": parser_query_intent,
            "parser_intent": parser_intent,
            "llm_intent": llm_intent,
            "normalized_intent": normalized_intent,
            "intent_selection": intent_selection,
            "llm_diff": self._summarize_intent_diff(parser_intent, llm_intent.get("intent")),
            "normalized_diff": self._summarize_intent_diff(
                llm_intent.get("intent"),
                normalized_intent.get("intent"),
            ),
            "classification": classification,
            "warnings": warnings,
            "semantic_diff": semantic_diff,
            "parser_signals": {
                "matched_metrics": list(parser_query_intent.matched_metrics),
                "matched_entities": list(parser_query_intent.matched_entities),
                "requested_dimensions": list(parser_query_intent.requested_dimensions),
                "filter_fields": [item.field for item in parser_query_intent.filters],
                "time_grain": parser_query_intent.time_context.grain,
                "has_version_context": parser_query_intent.version_context is not None,
                "requested_limit": parser_query_intent.requested_limit,
                "sort_fields": [item.field for item in parser_query_intent.requested_sort],
                "analysis_mode": parser_query_intent.analysis_mode,
                "subject_domain": parser_query_intent.subject_domain,
                "has_follow_up_cue": parser_query_intent.has_follow_up_cue,
                "has_explicit_slots": parser_query_intent.has_explicit_slots,
            },
            "classification_summary": {
                "question_type": classification.question_type,
                "subject_domain": classification.subject_domain,
                "inherit_context": classification.inherit_context,
                "need_clarification": classification.need_clarification,
                "reason_code": classification.reason_code,
                "confidence": classification.confidence,
            },
            "classifier_debug": classifier_debug,
        }

    def _build_llm_intent(
        self,
        *,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict[str, Any]:
        if self.intent_service is None:
            return {
                "status": "skipped",
                "reason": "intent service unavailable",
                "intent": None,
                "raw": None,
            }
        return self.intent_service.generate_intent(
            question=question,
            query_intent=query_intent,
            session_state=session_state,
        )

    def _normalize_intent(self, llm_intent: dict[str, Any]) -> dict[str, Any]:
        intent = llm_intent.get("intent")
        if intent is None or self.intent_normalizer is None:
            return {
                "status": "skipped",
                "intent": None,
                "warnings": [llm_intent.get("reason") or "llm intent unavailable"],
            }
        return self.intent_normalizer.normalize(intent)

    def _select_effective_query_intent(
        self,
        *,
        parser_query_intent: QueryIntent,
        normalized_intent_payload: dict[str, Any],
    ) -> tuple[QueryIntent, dict[str, Any]]:
        normalized_intent = normalized_intent_payload.get("intent")
        if normalized_intent is None:
            return QueryIntent(
                normalized_question=parser_query_intent.normalized_question,
                matched_metrics=[],
                matched_entities=[],
                requested_dimensions=[],
                filters=[],
                time_context=parser_query_intent.time_context.__class__(),
                version_context=None,
                requested_sort=[],
                requested_limit=None,
                analysis_mode=None,
                subject_domain="unknown",
                has_follow_up_cue=parser_query_intent.has_follow_up_cue,
                has_explicit_slots=False,
            ), {
                "selected_source": "none",
                "selection_reason": "llm intent unavailable; no execution baseline retained",
            }

        effective_query_intent = normalized_intent.to_query_intent(base_query_intent=parser_query_intent)
        return effective_query_intent, {
            "selected_source": "normalized",
            "selection_reason": "normalized intent selected",
        }

    def _summarize_intent_diff(
        self,
        left: StructuredIntent | None,
        right: StructuredIntent | None,
    ) -> dict[str, Any]:
        if left is None or right is None:
            return {
                "available": False,
                "reason": "one_side_missing",
            }

        return {
            "available": True,
            "subject_domain_changed": left.subject_domain != right.subject_domain,
            "analysis_mode_changed": left.analysis_mode != right.analysis_mode,
            "question_type_changed": left.question_type != right.question_type,
            "added_metrics": sorted(set(right.metrics) - set(left.metrics)),
            "removed_metrics": sorted(set(left.metrics) - set(right.metrics)),
            "added_entities": sorted(set(right.entities) - set(left.entities)),
            "removed_entities": sorted(set(left.entities) - set(right.entities)),
            "added_dimensions": sorted(set(right.dimensions) - set(left.dimensions)),
            "removed_dimensions": sorted(set(left.dimensions) - set(right.dimensions)),
            "added_filter_fields": sorted({item.field for item in right.filters} - {item.field for item in left.filters}),
            "removed_filter_fields": sorted({item.field for item in left.filters} - {item.field for item in right.filters}),
            "time_grain_changed": left.time_context.grain != right.time_context.grain,
            "version_changed": (
                (left.version_context.model_dump(mode="json") if left.version_context else None)
                != (right.version_context.model_dump(mode="json") if right.version_context else None)
            ),
        }

    def classify(
        self, question: str, session_state: SessionState | None = None
    ) -> tuple[QueryIntent, QuestionClassification, list[str]]:
        planning_trace = self.build_planning_trace(
            question=question,
            session_state=session_state,
        )
        return (
            planning_trace["query_intent"],
            planning_trace["classification"],
            planning_trace["warnings"],
        )

    def create_plan(
        self, question: str, session_state: SessionState | None = None
    ) -> tuple[QueryIntent, QuestionClassification, QueryPlan, list[str]]:
        planning_trace = self.build_planning_trace(
            question=question,
            session_state=session_state,
        )
        query_intent = planning_trace["query_intent"]
        classification = planning_trace["classification"]
        warnings = planning_trace["warnings"]

        matched_entities = query_intent.matched_entities
        matched_metrics = query_intent.matched_metrics
        filters = query_intent.filters
        time_context = query_intent.time_context
        version_context = query_intent.version_context
        analysis_mode = query_intent.analysis_mode
        sort = list(query_intent.requested_sort)
        limit = query_intent.requested_limit or self.semantic_runtime.default_limit(classification.subject_domain)
        requested_dimensions = list(query_intent.requested_dimensions)

        if classification.question_type == "follow_up" and session_state is not None:
            context_delta = classification.context_delta or self.semantic_runtime.build_context_delta(query_intent)
            merged = self.semantic_runtime.merge_with_session(
                session_state=session_state,
                query_intent=query_intent,
                context_delta=context_delta,
            )
            matched_entities = merged.matched_entities
            matched_metrics = merged.matched_metrics
            filters = merged.filters
            time_context = merged.time_context
            version_context = merged.version_context
            analysis_mode = merged.analysis_mode
            sort = list(merged.requested_sort)
            limit = merged.requested_limit or limit
            requested_dimensions = list(merged.requested_dimensions)

        query_plan = self.build_plan_from_intent(
            classification=classification,
            matched_metrics=matched_metrics,
            matched_entities=matched_entities,
            filters=filters,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=analysis_mode,
            sort=sort,
            limit=limit,
            requested_dimensions=requested_dimensions,
        )
        return query_intent, classification, query_plan, warnings

    def build_plan_from_intent(
        self,
        *,
        classification: QuestionClassification,
        query_intent: QueryIntent | None = None,
        session_state: SessionState | None = None,
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
            matched_metrics = list(query_intent.matched_metrics)
            filters = list(query_intent.filters)
            time_context = query_intent.time_context
            version_context = query_intent.version_context
            analysis_mode = query_intent.analysis_mode
            sort = list(query_intent.requested_sort)
            limit = query_intent.requested_limit or self.semantic_runtime.default_limit(classification.subject_domain)
            requested_dimensions = list(query_intent.requested_dimensions)

            if classification.question_type == "follow_up" and session_state is not None:
                context_delta = classification.context_delta or self.semantic_runtime.build_context_delta(query_intent)
                merged = self.semantic_runtime.merge_with_session(
                    session_state=session_state,
                    query_intent=query_intent,
                    context_delta=context_delta,
                )
                matched_entities = merged.matched_entities
                matched_metrics = merged.matched_metrics
                filters = merged.filters
                time_context = merged.time_context
                version_context = merged.version_context
                analysis_mode = merged.analysis_mode
                sort = list(merged.requested_sort)
                limit = merged.requested_limit or limit
                requested_dimensions = list(merged.requested_dimensions)

        matched_metrics = list(matched_metrics or [])
        matched_entities = list(matched_entities or [])
        filters = list(filters or [])
        requested_dimensions = list(requested_dimensions or [])
        sort = list(sort or [])
        if limit is None:
            limit = self.semantic_runtime.default_limit(classification.subject_domain)

        subject_domain = classification.subject_domain
        resolved_dimensions = self.semantic_runtime.suggest_dimensions(
            subject_domain=subject_domain,
            metrics=matched_metrics,
            requested_dimensions=requested_dimensions,
            analysis_mode=analysis_mode,
            limit=limit,
            sort=sort,
        )
        tables = self.semantic_runtime.select_tables(
            subject_domain=subject_domain,
            metrics=matched_metrics,
            dimensions=resolved_dimensions,
            filters=filters,
        )
        query_plan = QueryPlan(
            subject_domain=subject_domain,
            question_type=classification.question_type,
            metrics=matched_metrics,
            dimensions=resolved_dimensions,
            filters=filters,
            tables=tables,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=analysis_mode,
            sort=sort,
            limit=limit,
            entities=matched_entities,
            need_clarification=classification.need_clarification,
            clarification_question=classification.clarification_question,
        )
        return self.semantic_runtime.sanitize_query_plan(query_plan)
