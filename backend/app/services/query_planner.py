from __future__ import annotations

from typing import Any

from backend.app.models.classification import QuestionClassification, SemanticParse
from backend.app.models.query_plan import QueryPlan
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_classifier import QuestionClassifier
from backend.app.services.semantic_parser import SemanticParser
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryPlanner:
    def __init__(
        self,
        semantic_layer: dict[str, Any],
        semantic_runtime: SemanticRuntime | None = None,
        llm_client: LLMClient | None = None,
        prompt_builder: PromptBuilder | None = None,
        classification_llm_enabled: bool = False,
    ) -> None:
        self.semantic_layer = semantic_layer
        self.semantic_runtime = semantic_runtime or SemanticRuntime(semantic_layer)
        self.parser = SemanticParser(semantic_layer, semantic_runtime=self.semantic_runtime)
        self.classifier = QuestionClassifier(
            semantic_runtime=self.semantic_runtime,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            classification_llm_enabled=classification_llm_enabled,
        )

    def classify(
        self, question: str, session_state: SessionState | None = None
    ) -> tuple[SemanticParse, QuestionClassification, list[str]]:
        semantic_parse = self.parser.parse(question=question, session_state=session_state)
        classification, classifier_warnings = self.classifier.classify(
            question=question,
            semantic_parse=semantic_parse,
            session_state=session_state,
        )
        warnings: list[str] = list(classifier_warnings)
        if classification.need_clarification:
            warnings.append("clarification required before stable SQL generation")
        return semantic_parse, classification, warnings

    def create_plan(
        self, question: str, session_state: SessionState | None = None
    ) -> tuple[SemanticParse, QuestionClassification, QueryPlan, list[str]]:
        semantic_parse, classification, warnings = self.classify(
            question=question,
            session_state=session_state,
        )

        matched_entities = semantic_parse.matched_entities
        matched_metrics = semantic_parse.matched_metrics
        filters = semantic_parse.filters
        time_context = semantic_parse.time_context
        version_context = semantic_parse.version_context
        analysis_mode = semantic_parse.analysis_mode
        sort = list(semantic_parse.requested_sort)
        limit = semantic_parse.requested_limit or self.semantic_runtime.default_limit(classification.subject_domain)
        requested_dimensions = list(semantic_parse.requested_dimensions)

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
            analysis_mode=analysis_mode,
        )

        plan = QueryPlan(
            question_type=classification.question_type,
            subject_domain=classification.subject_domain,
            tables=self._pick_tables(classification.subject_domain, matched_metrics),
            semantic_views=self._pick_semantic_views(
                classification.subject_domain,
                matched_metrics=matched_metrics,
                dimensions=dimensions,
                filters=filters,
                version_field=version_context.field if version_context else None,
            ),
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
        plan = self.semantic_runtime.sanitize_query_plan(plan)
        if analysis_mode == "compare" and not plan.dimensions and not semantic_parse.requested_sort:
            plan.sort = []

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
            plan.semantic_views = []
            plan.metrics = []
            plan.dimensions = []
            plan.filters = []
        return semantic_parse, classification, plan, warnings

    def _pick_semantic_views(
        self,
        subject_domain: str,
        matched_metrics: list[str],
        dimensions: list[str],
        filters,
        version_field: str | None,
    ) -> list[str]:
        return self.semantic_runtime.rank_semantic_views(
            domain_name=subject_domain,
            metrics=matched_metrics,
            dimensions=dimensions,
            filters=filters,
            version_field=version_field,
        )

    def _pick_tables(self, subject_domain: str, matched_metrics: list[str]) -> list[str]:
        return self.semantic_runtime.resolve_tables_for_plan(subject_domain, matched_metrics)

    def _infer_dimensions(
        self,
        subject_domain: str,
        requested_dimensions: list[str],
        matched_entities: list[str],
        filters,
        time_context,
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
