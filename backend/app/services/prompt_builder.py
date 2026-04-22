from __future__ import annotations

from backend.app.models.classification import SemanticParse
from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class PromptBuilder:
    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime

    def build_query_plan_prompt(
        self,
        question: str,
        semantic_parse: SemanticParse,
        retrieval: RetrievalContext,
        base_plan: QueryPlan | None = None,
        session_state: SessionState | None = None,
    ) -> dict:
        return {
            "task": "query_plan_generation",
            "question": question,
            "subject_domain": semantic_parse.subject_domain,
            "metrics": semantic_parse.matched_metrics,
            "entities": semantic_parse.matched_entities,
            "session_semantic_diff": self._session_semantic_diff(semantic_parse, session_state),
            "retrieval_terms": retrieval.retrieval_terms,
            "retrieval_semantic_views": retrieval.semantic_views,
            "retrieval_hits": [hit.model_dump() for hit in retrieval.hits],
            "query_profile": self._query_profile(semantic_parse.subject_domain),
            "domain_tables": self._domain_tables(semantic_parse.subject_domain),
            "semantic_view_schemas": self._semantic_view_schemas(retrieval.semantic_views),
            "base_plan": base_plan.model_dump() if base_plan is not None else None,
            "session_state": session_state.model_dump() if session_state is not None else None,
            "instructions": {
                "return_format": "json",
                "constraints": [
                    "prefer selected semantic views over raw tables",
                    "only use registered domains, tables, semantic views, metrics and fields",
                    "for follow-up questions, preserve previous subject when the new question is only refining filters or time",
                ],
                "fields": [
                    "subject_domain",
                    "tables",
                    "semantic_views",
                    "metrics",
                    "dimensions",
                    "filters",
                    "version_context",
                    "sort",
                    "limit",
                    "join_path",
                    "reason",
                ],
            },
        }

    def build_classification_prompt(
        self,
        question: str,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
        semantic_diff: dict | None,
        base_classification: dict,
        allowed_question_types: list[str],
    ) -> dict:
        return {
            "task": "question_classification",
            "question": question,
            "semantic_parse": semantic_parse.model_dump(),
            "session_state": session_state.model_dump() if session_state is not None else None,
            "session_semantic_diff": semantic_diff,
            "base_classification": base_classification,
            "allowed_question_types": allowed_question_types,
            "instructions": {
                "return_format": "json",
                "fields": [
                    "question_type",
                    "subject_domain",
                    "inherit_context",
                    "confidence",
                    "reason",
                    "reason_code",
                    "clarification_question",
                ],
                "constraints": [
                    "choose question_type only from allowed_question_types",
                    "respect structured semantic_parse and session_semantic_diff",
                    "if the question is a follow-up refinement, keep inherit_context true",
                    "if classification is clarification_needed, provide clarification_question",
                ],
            },
        }

    def build_sql_prompt(self, query_plan: QueryPlan) -> dict:
        selected_sources = query_plan.semantic_views or query_plan.tables
        return {
            "task": "sql_generation",
            "query_plan": query_plan.model_dump(),
            "allowed_sources": selected_sources,
            "allowed_fields": sorted(self._allowed_fields(query_plan)),
            "instructions": {
                "return_format": "sql_only",
                "constraints": [
                    "readonly select only",
                    "must include limit",
                    "prefer semantic views when available",
                    "only reference sources from allowed_sources",
                    "do not reference fields outside allowed_fields",
                ],
            },
        }

    def _query_profile(self, subject_domain: str) -> dict | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.query_profile(subject_domain)

    def _session_semantic_diff(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
    ) -> dict | None:
        if self.semantic_runtime is None:
            return None
        return self.semantic_runtime.session_semantic_diff(semantic_parse, session_state)

    def _allowed_fields(self, query_plan: QueryPlan) -> set[str]:
        if self.semantic_runtime is None:
            return set()
        return self.semantic_runtime.allowed_fields_for_plan(query_plan)

    def _domain_tables(self, subject_domain: str) -> list[str] | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.domain_tables(subject_domain)

    def _semantic_view_schemas(self, semantic_views: list[str]) -> dict[str, list[str]]:
        if self.semantic_runtime is None:
            return {}
        return {
            view_name: self.semantic_runtime.semantic_view_fields(view_name)
            for view_name in semantic_views
        }
