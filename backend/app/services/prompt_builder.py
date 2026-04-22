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
            "base_plan": base_plan.model_dump() if base_plan is not None else None,
            "session_state": session_state.model_dump() if session_state is not None else None,
            "instructions": {
                "return_format": "json",
                "fields": [
                    "subject_domain",
                    "tables",
                    "semantic_views",
                    "metrics",
                    "dimensions",
                    "filters",
                    "join_path",
                    "reason",
                ],
            },
        }

    def build_sql_prompt(self, query_plan: QueryPlan) -> dict:
        return {
            "task": "sql_generation",
            "query_plan": query_plan.model_dump(),
            "instructions": {
                "return_format": "sql_only",
                "constraints": [
                    "readonly select only",
                    "must include limit",
                    "prefer semantic views when available",
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
