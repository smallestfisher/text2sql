from __future__ import annotations

from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext
from backend.app.services.semantic_runtime import SemanticRuntime


class QueryPlanCompiler:
    def __init__(self, semantic_runtime: SemanticRuntime, default_limit: int = 200) -> None:
        self.semantic_runtime = semantic_runtime
        self.default_limit = default_limit

    def compile(self, query_plan: QueryPlan, retrieval: RetrievalContext | None = None) -> QueryPlan:
        compiled = self._apply_retrieval_hints(query_plan, retrieval)
        return self.semantic_runtime.sanitize_query_plan(
            query_plan=compiled,
            default_limit=self.default_limit,
        )

    def _apply_retrieval_hints(
        self,
        query_plan: QueryPlan,
        retrieval: RetrievalContext | None,
    ) -> QueryPlan:
        if retrieval is None or not retrieval.hits:
            return query_plan

        compiled = query_plan.model_copy(deep=True)
        compiled = self._apply_retrieval_domain_hint(compiled, retrieval)
        compiled = self._apply_retrieval_table_hints(compiled, retrieval)
        return compiled

    def _apply_retrieval_domain_hint(
        self,
        query_plan: QueryPlan,
        retrieval: RetrievalContext,
    ) -> QueryPlan:
        if query_plan.subject_domain != "unknown":
            return query_plan

        for hit in retrieval.hits:
            candidate = self._hit_subject_domain(hit)
            if candidate and self.semantic_runtime.is_known_domain(candidate):
                return query_plan.model_copy(deep=True, update={"subject_domain": candidate})
        return query_plan

    def _apply_retrieval_table_hints(
        self,
        query_plan: QueryPlan,
        retrieval: RetrievalContext,
    ) -> QueryPlan:
        ordered_tables = list(query_plan.tables)
        existing_tables = set(ordered_tables)

        for hit in retrieval.hits[:4]:
            candidate_tables = self._hit_tables(hit)
            if not candidate_tables:
                continue

            if hit.source_type == "join_pattern":
                for table in candidate_tables:
                    if table not in existing_tables and self.semantic_runtime.is_known_table(table):
                        ordered_tables.append(table)
                        existing_tables.add(table)
                continue

            if hit.source_type == "example":
                if not existing_tables and hit.score < 2.0:
                    continue
                for table in candidate_tables:
                    if table not in existing_tables and self.semantic_runtime.is_known_table(table):
                        ordered_tables.append(table)
                        existing_tables.add(table)

            if hit.source_type == "knowledge" and hit.metadata.get("kind") == "table_metadata":
                table = hit.metadata.get("table")
                if isinstance(table, str) and table not in existing_tables and self.semantic_runtime.is_known_table(table):
                    ordered_tables.append(table)
                    existing_tables.add(table)

        return query_plan.model_copy(deep=True, update={"tables": ordered_tables})

    def _hit_subject_domain(self, hit) -> str | None:
        subject_domain = hit.metadata.get("subject_domain")
        if isinstance(subject_domain, str) and subject_domain:
            return subject_domain
        domains = hit.metadata.get("domains", [])
        if isinstance(domains, list) and len(domains) == 1 and isinstance(domains[0], str):
            return domains[0]
        return None

    def _hit_tables(self, hit) -> set[str]:
        tables = hit.metadata.get("tables", [])
        if not isinstance(tables, list):
            return set()
        return {str(item) for item in tables if item}
