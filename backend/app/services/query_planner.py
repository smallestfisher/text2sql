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
        classification_llm_enabled: bool = False,
        intent_shadow_enabled: bool = False,
        intent_primary_enabled: bool = False,
        intent_fallback_enabled: bool = True,
    ) -> None:
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime or SemanticRuntime(domain_config)
        self.parser = QueryIntentParser(domain_config, semantic_runtime=self.semantic_runtime)
        self.classifier = QuestionClassifier(
            semantic_runtime=self.semantic_runtime,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            classification_llm_enabled=classification_llm_enabled,
        )
        self.intent_service = intent_service
        self.intent_normalizer = intent_normalizer
        self.intent_shadow_enabled = intent_shadow_enabled
        self.intent_primary_enabled = intent_primary_enabled
        self.intent_fallback_enabled = intent_fallback_enabled

    def build_planning_trace(
        self,
        question: str,
        session_state: SessionState | None = None,
    ) -> dict[str, Any]:
        parser_query_intent = self.parser.parse(question=question, session_state=session_state)
        parser_intent = StructuredIntent.from_query_intent(parser_query_intent)
        shadow_intent = self._build_shadow_intent(
            question=question,
            query_intent=parser_query_intent,
            session_state=session_state,
        )
        normalized_shadow_intent = self._normalize_shadow_intent(shadow_intent)
        query_intent, intent_selection = self._select_effective_query_intent(
            parser_query_intent=parser_query_intent,
            normalized_shadow_intent=normalized_shadow_intent,
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
            "shadow_intent": shadow_intent,
            "normalized_shadow_intent": normalized_shadow_intent,
            "intent_selection": intent_selection,
            "shadow_diff": self._summarize_intent_diff(parser_intent, shadow_intent.get("intent")),
            "normalized_diff": self._summarize_intent_diff(
                shadow_intent.get("intent"),
                normalized_shadow_intent.get("intent"),
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

    def _build_shadow_intent(
        self,
        *,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict[str, Any]:
        if not self.intent_shadow_enabled or self.intent_service is None:
            return {
                "status": "skipped",
                "reason": "intent shadow disabled",
                "intent": None,
                "raw": None,
            }
        return self.intent_service.generate_shadow_intent(
            question=question,
            query_intent=query_intent,
            session_state=session_state,
        )

    def _normalize_shadow_intent(self, shadow_intent: dict[str, Any]) -> dict[str, Any]:
        intent = shadow_intent.get("intent")
        if intent is None or self.intent_normalizer is None:
            return {
                "status": "skipped",
                "intent": None,
                "warnings": [shadow_intent.get("reason") or "shadow intent unavailable"],
            }
        return self.intent_normalizer.normalize(intent)

    def _select_effective_query_intent(
        self,
        *,
        parser_query_intent: QueryIntent,
        normalized_shadow_intent: dict[str, Any],
    ) -> tuple[QueryIntent, dict[str, Any]]:
        normalized_intent = normalized_shadow_intent.get("intent")
        fallback_reason: str | None = None

        if not self.intent_primary_enabled:
            return parser_query_intent, {
                "selected_source": "parser",
                "fallback_used": False,
                "fallback_reason": "intent primary disabled",
            }

        if normalized_intent is None:
            fallback_reason = "normalized intent unavailable"
        elif normalized_intent.confidence is not None and normalized_intent.confidence < 0.45:
            fallback_reason = f"normalized intent confidence too low: {normalized_intent.confidence:.3f}"
        elif (
            normalized_intent.subject_domain == "unknown"
            and not normalized_intent.metrics
            and not normalized_intent.dimensions
            and not normalized_intent.filters
        ):
            fallback_reason = "normalized intent has no actionable slots"

        if fallback_reason is not None:
            if self.intent_fallback_enabled:
                return parser_query_intent, {
                    "selected_source": "parser",
                    "fallback_used": True,
                    "fallback_reason": fallback_reason,
                }
            return parser_query_intent, {
                "selected_source": "parser",
                "fallback_used": False,
                "fallback_reason": fallback_reason,
            }

        effective_query_intent = normalized_intent.to_query_intent(base_query_intent=parser_query_intent)
        return effective_query_intent, {
            "selected_source": "normalized",
            "fallback_used": False,
            "fallback_reason": None,
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

        if classification.inherit_context and session_state is not None:
            matched_entities = matched_entities or session_state.entities
            matched_metrics = matched_metrics or session_state.metrics
            filters = self._merge_filters(
                session_state.filters,
                filters,
                remove_fields=classification.context_delta.remove_filters,
            )
            if time_context.grain == "unknown" and session_state.time_context is not None:
                time_context = session_state.time_context
            if version_context is None:
                version_context = session_state.version_context
            if analysis_mode is None:
                analysis_mode = session_state.analysis_mode
            if not requested_dimensions:
                requested_dimensions = list(session_state.dimensions)
            sort = classification.context_delta.replace_sort or session_state.sort
            limit = classification.context_delta.replace_limit or session_state.limit or limit

        dimensions = self._infer_dimensions(
            subject_domain=classification.subject_domain,
            requested_dimensions=requested_dimensions,
            matched_entities=matched_entities,
            filters=filters,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=analysis_mode,
        )

        plan = self._create_query_plan(
            query_intent=query_intent,
            classification=classification,
            matched_entities=matched_entities,
            matched_metrics=matched_metrics,
            filters=filters,
            dimensions=dimensions,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=analysis_mode,
            sort=sort,
            limit=limit,
        )
        plan = self.semantic_runtime.sanitize_query_plan(plan)
        self._apply_post_plan_adjustments(plan, query_intent, classification)

        if plan.need_clarification:
            classification.question_type = "clarification_needed"
            classification.need_clarification = True
            classification.reason = plan.reason
            classification.reason_code = plan.reason_code
            classification.clarification_question = plan.clarification_question
            if "clarification required before stable SQL generation" not in warnings:
                warnings.append("clarification required before stable SQL generation")
        if classification.question_type == "invalid":
            plan.tables = []
            plan.metrics = []
            plan.dimensions = []
            plan.filters = []
        return query_intent, classification, plan, warnings

    def build_plan_from_intent(
        self,
        *,
        query_intent: QueryIntent,
        classification: QuestionClassification,
        session_state: SessionState | None = None,
    ) -> QueryPlan:
        matched_entities = query_intent.matched_entities
        matched_metrics = query_intent.matched_metrics
        filters = query_intent.filters
        time_context = query_intent.time_context
        version_context = query_intent.version_context
        analysis_mode = query_intent.analysis_mode
        sort = list(query_intent.requested_sort)
        limit = query_intent.requested_limit or self.semantic_runtime.default_limit(classification.subject_domain)
        requested_dimensions = list(query_intent.requested_dimensions)

        if classification.inherit_context and session_state is not None:
            matched_entities = matched_entities or session_state.entities
            matched_metrics = matched_metrics or session_state.metrics
            filters = self._merge_filters(
                session_state.filters,
                filters,
                remove_fields=classification.context_delta.remove_filters,
            )
            if time_context.grain == "unknown" and session_state.time_context is not None:
                time_context = session_state.time_context
            if version_context is None:
                version_context = session_state.version_context
            if analysis_mode is None:
                analysis_mode = session_state.analysis_mode
            if not requested_dimensions:
                requested_dimensions = list(session_state.dimensions)
            sort = classification.context_delta.replace_sort or session_state.sort
            limit = classification.context_delta.replace_limit or session_state.limit or limit

        dimensions = self._infer_dimensions(
            subject_domain=classification.subject_domain,
            requested_dimensions=requested_dimensions,
            matched_entities=matched_entities,
            filters=filters,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=analysis_mode,
        )

        plan = self._create_query_plan(
            query_intent=query_intent,
            classification=classification,
            matched_entities=matched_entities,
            matched_metrics=matched_metrics,
            filters=filters,
            dimensions=dimensions,
            time_context=time_context,
            version_context=version_context,
            analysis_mode=analysis_mode,
            sort=sort,
            limit=limit,
        )
        plan = self.semantic_runtime.sanitize_query_plan(plan)
        self._apply_post_plan_adjustments(plan, query_intent, classification)

        if classification.question_type == "invalid":
            plan.tables = []
            plan.metrics = []
            plan.dimensions = []
            plan.filters = []

        return plan

    def _create_query_plan(
        self,
        *,
        query_intent: QueryIntent,
        classification: QuestionClassification,
        matched_entities: list[str],
        matched_metrics: list[str],
        filters,
        dimensions: list[str],
        time_context,
        version_context,
        analysis_mode: str | None,
        sort,
        limit: int,
    ) -> QueryPlan:
        return QueryPlan(
            question_type=classification.question_type,
            subject_domain=classification.subject_domain,
            tables=self._pick_tables(classification.subject_domain, matched_metrics),
            entities=matched_entities,
            metrics=matched_metrics,
            dimensions=dimensions,
            filters=filters,
            join_path=[],
            time_context=time_context,
            version_context=version_context,
            inherit_context=classification.inherit_context,
            context_delta=classification.context_delta,
            need_clarification=classification.need_clarification,
            clarification_question=classification.clarification_question,
            reason_code=classification.reason_code,
            analysis_mode=analysis_mode,
            sort=sort,
            limit=limit,
            reason=classification.reason,
        )

    def _apply_post_plan_adjustments(
        self,
        plan: QueryPlan,
        query_intent: QueryIntent,
        classification: QuestionClassification,
    ) -> None:
        analysis_mode = query_intent.analysis_mode
        if (
            query_intent.requested_limit is not None
            and not query_intent.requested_dimensions
            and analysis_mode != "trend"
        ):
            metric_fields = {
                self.semantic_runtime.metric_column(metric_name)
                for metric_name in plan.metrics
            }
            if any(item.field in metric_fields for item in plan.sort):
                plan.dimensions = [
                    item for item in plan.dimensions if item not in {"biz_date", "biz_month"}
                ]
        if analysis_mode == "compare" and not query_intent.requested_sort:
            if "biz_month" in plan.dimensions:
                plan.sort = [SortItem(field="biz_month", order="asc")]
            elif "biz_date" in plan.dimensions:
                plan.sort = [SortItem(field="biz_date", order="asc")]
            elif not plan.dimensions:
                plan.sort = []

        if (
            plan.subject_domain == "demand"
            and plan.dimensions == ["demand_month"]
            and "demand_qty" in plan.metrics
            and not query_intent.requested_sort
        ):
            plan.sort = [SortItem(field="demand_month", order="asc")]

        if plan.need_clarification:
            classification.question_type = "clarification_needed"
            classification.need_clarification = True
            classification.reason = plan.reason
            classification.reason_code = plan.reason_code
            classification.clarification_question = plan.clarification_question

    def _pick_tables(self, subject_domain: str, matched_metrics: list[str]) -> list[str]:
        return self.semantic_runtime.resolve_tables_for_plan(subject_domain, matched_metrics)

    def _infer_dimensions(
        self,
        subject_domain: str,
        requested_dimensions: list[str],
        matched_entities: list[str],
        filters,
        time_context,
        version_context,
        analysis_mode: str | None = None,
    ) -> list[str]:
        filter_fields = {item.field for item in filters}
        dimensions = self.semantic_runtime.suggest_dimensions(
            subject_domain=subject_domain,
            requested_dimensions=requested_dimensions,
            matched_entities=matched_entities,
            filter_fields=filter_fields,
            time_grain=time_context.grain,
        )
        if analysis_mode == 'trend':
            preferred_time_dimension = 'biz_month' if time_context.grain == 'month' else 'biz_date'
            if preferred_time_dimension not in dimensions:
                dimensions = dimensions + [preferred_time_dimension]
        if version_context is not None and 'PM_VERSION' in dimensions and 'PM_VERSION' not in requested_dimensions:
            dimensions = [item for item in dimensions if item != 'PM_VERSION']
        return dimensions

    def _merge_filters(self, current_filters, new_filters, remove_fields=None):
        remove_fields = set(remove_fields or [])
        merged = {
            self._filter_key(item): item
            for item in current_filters
            if item.field not in remove_fields
        }
        for item in new_filters:
            merged[self._filter_key(item)] = item
        return list(merged.values())

    def _filter_key(self, filter_item) -> str:
        return f"{filter_item.field}:{filter_item.op}"
