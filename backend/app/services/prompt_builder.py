from __future__ import annotations

import json
import re

from backend.app.config import BUSINESS_KNOWLEDGE_PATH, EXAMPLES_TEMPLATE_PATH, TABLES_METADATA_PATH
from backend.app.models.classification import QueryIntent
from backend.app.models.example_library import ExampleRecord
from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class PromptBuilder:
    BUSINESS_NOTES_MAX_CHARS = 2400

    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime
        self._tables_metadata = self._load_tables_metadata()
        self._business_knowledge = self._load_business_knowledge()

    def build_classification_prompt(
        self,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
        semantic_diff: dict | None,
        base_classification: dict,
        allowed_question_types: list[str],
        candidate_scores: dict[str, float] | None = None,
        arbitration_context: dict | None = None,
    ) -> dict:
        evidence = self._classification_evidence(
            query_intent=query_intent,
            session_state=session_state,
            semantic_diff=semantic_diff,
        )
        return {
            "task": "question_classification",
            "question": question,
            "query_intent": query_intent.model_dump(),
            "session_state": session_state.model_dump() if session_state is not None else None,
            "session_semantic_diff": semantic_diff,
            "classification_evidence": evidence,
            "base_classification": base_classification,
            "allowed_question_types": allowed_question_types,
            "candidate_scores": candidate_scores or {},
            "arbitration_context": arbitration_context or {},
            "instructions": {
                "return_format": "json",
                "fields": self._prompt_asset_strings("classification", "fields"),
                "category_definitions": self._prompt_asset_dict("classification", "category_definitions"),
                "context_delta_field_guide": self._prompt_asset_dict("classification", "context_delta_field_guide"),
                "context_delta_rules": self._prompt_asset_strings("classification", "context_delta_rules"),
                "context_delta_examples": self._classification_delta_examples(),
                "arbitration_checklist": self._prompt_asset_strings("classification", "arbitration_checklist"),
                "business_few_shots": self._classification_business_examples(),
                "constraints": self._prompt_asset_strings("classification", "constraints"),
            },
        }

    def build_relevance_prompt(
        self,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict:
        return {
            "task": "question_relevance_guard",
            "question": question,
            "semantic_signals": {
                "subject_domain": query_intent.subject_domain,
                "matched_metrics": query_intent.matched_metrics,
                "matched_entities": query_intent.matched_entities,
                "requested_dimensions": query_intent.requested_dimensions,
                "filter_fields": [item.field for item in query_intent.filters],
                "time_grain": query_intent.time_context.grain,
                "has_version_context": query_intent.version_context is not None,
                "has_follow_up_cue": query_intent.has_follow_up_cue,
                "has_explicit_slots": query_intent.has_explicit_slots,
            },
            "session_context": {
                "subject_domain": session_state.subject_domain if session_state is not None else None,
                "metrics": session_state.metrics if session_state is not None else [],
                "dimensions": session_state.dimensions if session_state is not None else [],
                "filter_fields": [item.field for item in session_state.filters] if session_state is not None else [],
            },
            "system_scope": {
                "supported_domains": self._supported_domains(),
                "supported_intent": "企业业务数据分析问题，能够映射为针对 inventory、demand、plan_actual、sales_financial、dimension 等数据的只读 SQL。",
                "in_scope_examples": self._prompt_asset_strings("relevance", "in_scope_examples"),
                "out_of_scope_examples": self._prompt_asset_strings("relevance", "out_of_scope_examples"),
            },
            "instructions": {
                "return_format": "json",
                "fields": self._prompt_asset_strings("relevance", "fields"),
                "decision_values": self._prompt_asset_dict("relevance", "decision_values"),
                "constraints": self._prompt_asset_strings("relevance", "constraints"),
            },
        }

    def build_intent_prompt(
        self,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict:
        subject_domain = query_intent.subject_domain
        domain_tables = self._domain_tables(subject_domain) if subject_domain != "unknown" else []
        domain_fields: list[str] = []
        for table_name in domain_tables:
            table_meta = self._tables_metadata.get(table_name, {})
            columns = table_meta.get("columns", []) if isinstance(table_meta, dict) else []
            for column in columns:
                column_name = column.get("name") if isinstance(column, dict) else None
                if isinstance(column_name, str) and column_name:
                    domain_fields.append(column_name)
        business_notes = self._business_notes(subject_domain)
        return {
            "task": "intent_understanding",
            "question": question,
            "shallow_parse": query_intent.model_dump(),
            "session_state": session_state.model_dump() if session_state is not None else None,
            "domain_hints": {
                "subject_domain": subject_domain,
                "domain_tables": domain_tables,
                "domain_fields": sorted(set(domain_fields))[:120],
                "semantic_fields": self._semantic_fields(subject_domain),
                "supported_domains": self._supported_domains(),
            },
            "business_knowledge": business_notes,
            "instructions": {
                "return_format": "json",
                "fields": self._prompt_asset_strings("intent_understanding", "fields"),
                "constraints": self._prompt_asset_strings("intent_understanding", "constraints"),
            },
        }

    def build_sql_prompt(
        self,
        query_plan: QueryPlan,
        retrieval: RetrievalContext | None = None,
        question: str | None = None,
    ) -> dict:
        selected_sources = query_plan.tables or self._domain_tables(query_plan.subject_domain) or []
        source_schemas = {
            table_name: self._tables_metadata.get(table_name, {})
            for table_name in selected_sources
            if table_name in self._tables_metadata
        }
        field_resolution = self._field_resolution(query_plan)
        shape_contract = self._shape_contract(query_plan)
        retrieved_examples = self._select_retrieved_examples(query_plan, retrieval)
        sql_preferences = self._prompt_asset_strings("sql_generation", "base_preferences")
        if shape_contract["required_projection"]:
            sql_preferences = [
                "把 query_plan.dimensions 当成硬 contract：每个 dimension 都必须在最终外层 SELECT 中显式投影；只要存在聚合指标，这些 dimension 也必须在最终外层 GROUP BY 中逐一出现。",
                *sql_preferences,
            ]
        if shape_contract["dimension_hints"]:
            sql_preferences = [
                *shape_contract["dimension_hints"],
                *sql_preferences,
            ]
        if self._has_latest_n_filter(query_plan):
            sql_preferences = [
                *self._prompt_asset_strings("sql_generation", "latest_n_preferences"),
                *sql_preferences,
            ]
        business_notes = self._business_notes_for_plan(query_plan, selected_sources, retrieval)
        business_notes_source = self._business_notes_source_for_plan(query_plan, selected_sources, retrieval)
        context_budget = {
            "business_notes_max_chars": self.BUSINESS_NOTES_MAX_CHARS,
            "business_notes_mode": "ranked_relevant_chunks",
            "tables_metadata_mode": "selected_query_plan_tables_only",
        }
        context_summary = {
            "selected_sources": selected_sources,
            "tables_metadata_count": len(source_schemas),
            "business_notes_chars": len(business_notes),
            "business_notes_source": business_notes_source,
            "few_shot_used": bool(retrieved_examples),
            "retrieved_example_count": len(retrieved_examples),
            "retrieved_example_ids": [item["id"] for item in retrieved_examples],
            "subject_domain": query_plan.subject_domain,
            "business_knowledge_entry_ids": self._selected_business_knowledge_ids(query_plan, selected_sources, retrieval),
            "join_pattern_ids": self._selected_join_pattern_ids(retrieval),
        }
        return {
            "task": "sql_generation",
            "question": question,
            "query_plan": query_plan.model_dump(),
            "allowed_sources": selected_sources,
            "allowed_fields": sorted(self._sql_allowed_fields(query_plan)),
            "field_resolution": field_resolution,
            "shape_contract": shape_contract,
            "tables_metadata": source_schemas,
            "business_notes": business_notes,
            "join_patterns": self._selected_join_patterns(retrieval),
            "context_budget": context_budget,
            "context_summary": context_summary,
            "instructions": {
                "return_format": "sql_only",
                "constraints": self._prompt_asset_strings("sql_generation", "base_constraints"),
                "sql_preferences": sql_preferences,
                "few_shot": {
                    "retrieved_examples": retrieved_examples,
                },
            },
        }

    def _load_tables_metadata(self) -> dict:
        try:
            return json.loads(TABLES_METADATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_examples(self) -> dict[str, ExampleRecord]:
        try:
            payload = json.loads(EXAMPLES_TEMPLATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        examples: dict[str, ExampleRecord] = {}
        for item in payload if isinstance(payload, list) else []:
            try:
                example = ExampleRecord(**item)
            except Exception:
                continue
            examples[example.id] = example
        return examples

    def _load_business_knowledge(self) -> list[dict]:
        try:
            payload = json.loads(BUSINESS_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
        entries = payload.get("entries", [])
        return entries if isinstance(entries, list) else []

    def _business_notes_for_plan(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> str:
        return self._structured_business_notes_for_plan(query_plan, selected_sources, retrieval)

    def _business_notes_source_for_plan(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> str:
        selected_entries = self._select_business_knowledge_entries(query_plan, selected_sources, retrieval)
        if not selected_entries:
            return "none"
        if self._retrieved_knowledge_hit_scores(retrieval):
            return "structured_knowledge+retrieval"
        return "structured_knowledge"

    def _structured_business_notes_for_plan(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> str:
        selected_entries = self._select_business_knowledge_entries(query_plan, selected_sources, retrieval)
        if not selected_entries:
            return ""
        sections: list[str] = []
        total_chars = 0
        for entry in selected_entries:
            notes = entry.get("notes", [])
            if not isinstance(notes, list) or not notes:
                continue
            title = str(entry.get("id", "business_note"))
            tables = ", ".join(entry.get("tables", [])) if isinstance(entry.get("tables"), list) else ""
            lines = [f"[{title}]"]
            if tables:
                lines.append(f"相关表: {tables}")
            for note in notes:
                lines.append(f"- {note}")
            block = "\n".join(lines)
            separator_chars = 2 if sections else 0
            projected = total_chars + separator_chars + len(block)
            if projected > self.BUSINESS_NOTES_MAX_CHARS and sections:
                continue
            sections.append(block)
            total_chars = projected
            if total_chars >= self.BUSINESS_NOTES_MAX_CHARS:
                break
        join_pattern_sections = self._retrieved_join_pattern_blocks(retrieval)
        for block in join_pattern_sections:
            separator_chars = 2 if sections else 0
            projected = total_chars + separator_chars + len(block)
            if projected > self.BUSINESS_NOTES_MAX_CHARS and sections:
                continue
            sections.append(block)
            total_chars = projected
            if total_chars >= self.BUSINESS_NOTES_MAX_CHARS:
                break
        return "\n\n".join(sections)[: self.BUSINESS_NOTES_MAX_CHARS]

    def _business_note_terms(self, query_plan: QueryPlan, selected_sources: list[str] | None) -> set[str]:
        terms = {
            query_plan.subject_domain,
            *(selected_sources or []),
            *query_plan.tables,
            *query_plan.metrics,
            *query_plan.dimensions,
            *(item.field for item in query_plan.filters),
        }
        if query_plan.version_context and query_plan.version_context.field:
            terms.add(query_plan.version_context.field)
        return {str(term).lower() for term in terms if term}

    def _select_business_knowledge_entries(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> list[dict]:
        if not self._business_knowledge:
            return []
        terms = self._business_note_terms(query_plan, selected_sources)
        knowledge_hit_scores = self._retrieved_knowledge_hit_scores(retrieval)
        selected: list[tuple[float, int, dict]] = []
        for index, entry in enumerate(self._business_knowledge):
            score = self._score_business_knowledge_entry(
                query_plan,
                selected_sources,
                terms,
                entry,
                knowledge_hit_scores,
            )
            if score <= 0:
                continue
            selected.append((score, -index, entry))
        selected.sort(reverse=True)
        return [entry for _score, _negative_index, entry in selected]

    def _selected_business_knowledge_ids(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> list[str]:
        return [
            str(entry.get("id"))
            for entry in self._select_business_knowledge_entries(query_plan, selected_sources, retrieval)
            if entry.get("id")
        ]

    def _score_business_knowledge_entry(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        terms: set[str],
        entry: dict,
        knowledge_hit_scores: dict[str, float] | None = None,
    ) -> float:
        score = 0.0
        domains = {str(item).lower() for item in entry.get("domains", []) if item}
        if query_plan.subject_domain and query_plan.subject_domain.lower() in domains:
            score += 5
        entry_tables = {str(item).lower() for item in entry.get("tables", []) if item}
        for source in selected_sources or []:
            if source.lower() in entry_tables:
                score += 4
        for table_name in query_plan.tables:
            if table_name.lower() in entry_tables:
                score += 4
        entry_keywords = {str(item).lower() for item in entry.get("keywords", []) if item}
        score += sum(1 for term in terms if term in entry_keywords)
        entry_id = str(entry.get("id", ""))
        if entry_id and knowledge_hit_scores and entry_id in knowledge_hit_scores:
            score += 6 + knowledge_hit_scores[entry_id]
        return score

    def _retrieved_knowledge_hit_scores(
        self,
        retrieval: RetrievalContext | None,
    ) -> dict[str, float]:
        if retrieval is None:
            return {}
        scores: dict[str, float] = {}
        for hit in retrieval.hits:
            if hit.source_type != "knowledge":
                continue
            entry_id = hit.source_id.removeprefix("business_knowledge:")
            if entry_id == hit.source_id:
                continue
            scores[entry_id] = max(scores.get(entry_id, 0.0), hit.score)
        return scores

    def _retrieved_join_pattern_blocks(
        self,
        retrieval: RetrievalContext | None,
    ) -> list[str]:
        if retrieval is None:
            return []
        sections: list[str] = []
        for hit in retrieval.hits:
            if hit.source_type != "join_pattern":
                continue
            lines = [f"[join_pattern:{hit.source_id}]"]
            tables = hit.metadata.get("tables", [])
            join_path = hit.metadata.get("join_path", [])
            notes = hit.metadata.get("notes", [])
            if isinstance(tables, list) and tables:
                lines.append("相关表: " + ", ".join(str(item) for item in tables if item))
            if isinstance(join_path, list):
                for item in join_path:
                    if item:
                        lines.append(f"- join: {item}")
            if isinstance(notes, list):
                for item in notes:
                    if item:
                        lines.append(f"- {item}")
            sections.append("\n".join(lines))
        return sections

    def _select_retrieved_examples(
        self,
        query_plan: QueryPlan,
        retrieval: RetrievalContext | None,
    ) -> list[dict]:
        if retrieval is None:
            return []

        examples = self._load_examples()
        selected: list[dict] = []
        for hit in retrieval.hits:
            if hit.source_type != "example":
                continue
            example = examples.get(hit.source_id)
            if example is None:
                continue
            if not self._retrieved_example_matches_plan(query_plan, example, hit):
                continue
            selected.append(
                {
                    "id": example.id,
                    "question": example.question,
                    "intent": example.intent,
                    "tables": example.tables,
                    "metrics": example.metrics,
                    "dimensions": example.dimensions,
                    "filters": [item.model_dump(mode="json") for item in example.filters],
                    "sql": example.sql,
                    "result_shape": example.result_shape,
                    "notes": example.notes,
                    "matched_features": hit.matched_features,
                }
            )
            if len(selected) >= 2:
                break
        return selected

    def _selected_join_patterns(self, retrieval: RetrievalContext | None) -> list[dict]:
        if retrieval is None:
            return []
        selected: list[dict] = []
        for hit in retrieval.hits:
            if hit.source_type != "join_pattern":
                continue
            selected.append(
                {
                    "id": hit.source_id,
                    "summary": hit.summary,
                    "matched_features": hit.matched_features,
                    "domains": hit.metadata.get("domains", []),
                    "tables": hit.metadata.get("tables", []),
                    "join_path": hit.metadata.get("join_path", []),
                    "notes": hit.metadata.get("notes", []),
                }
            )
        return selected[:2]

    def _selected_join_pattern_ids(self, retrieval: RetrievalContext | None) -> list[str]:
        return [item["id"] for item in self._selected_join_patterns(retrieval)]

    def _retrieved_example_matches_plan(
        self,
        query_plan: QueryPlan,
        example: ExampleRecord,
        hit: RetrievalHit,
    ) -> bool:
        if example.subject_domain == query_plan.subject_domain:
            return True

        plan_tables = set(query_plan.tables)
        if plan_tables and plan_tables.intersection(example.tables):
            return True

        plan_metrics = set(query_plan.metrics)
        if plan_metrics and plan_metrics.intersection(example.metrics):
            return True

        plan_filter_fields = {item.field for item in query_plan.filters}
        example_filter_fields = {item.field for item in example.filters}
        if plan_filter_fields and plan_filter_fields.intersection(example_filter_fields):
            return True

        return bool(
            hit.score >= 2.0
            and any(
                feature.startswith(("metrics:", "filters:", "version:", "time_", "metric:"))
                for feature in hit.matched_features
            )
        )

    def _query_profile(self, subject_domain: str) -> dict | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.query_profile(subject_domain)

    def _session_semantic_diff(
        self,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict | None:
        if self.semantic_runtime is None:
            return None
        return self.semantic_runtime.session_semantic_diff(query_intent, session_state)

    def _allowed_fields(self, query_plan: QueryPlan) -> set[str]:
        if self.semantic_runtime is None:
            return set()
        return self.semantic_runtime.allowed_fields_for_plan(query_plan)

    def _sql_allowed_fields(self, query_plan: QueryPlan) -> set[str]:
        if self.semantic_runtime is None:
            return self._allowed_fields(query_plan)

        fields: set[str] = set()
        for table_name in query_plan.tables:
            fields.update(self.semantic_runtime.table_fields(table_name))
        for metric_name in query_plan.metrics:
            fields.update(
                self.semantic_runtime.metric_expression_columns(
                    metric_name,
                    table_names=query_plan.tables,
                )
            )
        return fields or self._allowed_fields(query_plan)

    def _field_resolution(self, query_plan: QueryPlan) -> dict[str, dict[str, list[str]]]:
        return {
            "dimensions": self._field_resolution_map(query_plan, query_plan.dimensions),
            "filters": self._field_resolution_map(
                query_plan,
                [item.field for item in query_plan.filters],
            ),
            "metrics": {
                metric_name: self._physical_metric_candidates(query_plan, metric_name)
                for metric_name in query_plan.metrics
                if self._physical_metric_candidates(query_plan, metric_name)
            },
            "sort": self._field_resolution_map(
                query_plan,
                [item.field for item in query_plan.sort],
            ),
        }

    def _field_resolution_map(
        self,
        query_plan: QueryPlan,
        fields: list[str],
    ) -> dict[str, list[str]]:
        resolved: dict[str, list[str]] = {}
        for field in fields:
            physical_candidates = self._physical_candidates(query_plan, field)
            if physical_candidates:
                resolved[field] = physical_candidates
        return resolved

    def _physical_candidates(self, query_plan: QueryPlan, logical_field: str) -> list[str]:
        if self.semantic_runtime is None:
            return []
        resolved = self.semantic_runtime.resolve_field_candidates(
            query_plan.subject_domain,
            query_plan.tables,
            logical_field,
        )
        physical_allowed = self._sql_allowed_fields(query_plan)
        allowed_candidates = sorted(item for item in resolved if item in physical_allowed)
        qualified = self._qualify_columns(query_plan, allowed_candidates)
        return qualified or allowed_candidates

    def _physical_metric_candidates(self, query_plan: QueryPlan, metric_name: str) -> list[str]:
        if self.semantic_runtime is None:
            return []
        metric_columns = sorted(
            self.semantic_runtime.metric_expression_columns(
                metric_name,
                table_names=query_plan.tables,
            )
        )
        qualified = self._qualify_columns(query_plan, metric_columns)
        return qualified or metric_columns

    def _shape_contract(self, query_plan: QueryPlan) -> dict:
        required_projection = list(query_plan.dimensions)
        aggregate_metrics = list(query_plan.metrics)
        dimension_hints: list[str] = []
        logical_dimension_examples: dict[str, list[str]] = {}
        for field in required_projection:
            examples = self._logical_dimension_examples(query_plan, field)
            if examples:
                logical_dimension_examples[field] = examples
                if field == "biz_month":
                    dimension_hints.append(
                        "若 biz_month 来自月表字段，可直接投影真实月份列并别名成 biz_month；若来自日报字段，需在外层 SELECT 中显式写出月表达式，例如 DATE_FORMAT(<date_col>, '%Y-%m') AS biz_month，并在外层 GROUP BY 中使用相同表达式或别名。"
                    )
        return {
            "required_projection": required_projection,
            "required_group_by": required_projection if aggregate_metrics else [],
            "aggregate_metrics": aggregate_metrics,
            "logical_dimension_examples": logical_dimension_examples,
            "dimension_hints": dimension_hints,
        }

    def _logical_dimension_examples(self, query_plan: QueryPlan, logical_field: str) -> list[str]:
        if logical_field != "biz_month":
            return []
        examples: list[str] = []
        if self.semantic_runtime is None:
            return examples
        month_format = self._biz_month_date_format(query_plan)
        physical_candidates = self._physical_candidates(query_plan, logical_field)
        for candidate in physical_candidates:
            if candidate.endswith(".report_month") or candidate == "report_month":
                examples.append(f"{candidate} AS biz_month")
            elif candidate.endswith(".plan_month") or candidate == "plan_month":
                examples.append(f"{candidate} AS biz_month")
        date_candidates = self._physical_candidates(query_plan, "biz_date")
        for candidate in date_candidates:
            if self._looks_like_date_column(candidate):
                examples.append(f"DATE_FORMAT({candidate}, '{month_format}') AS biz_month")
        unique_examples: list[str] = []
        for item in examples:
            if item not in unique_examples:
                unique_examples.append(item)
        return unique_examples

    def _looks_like_date_column(self, candidate: str) -> bool:
        normalized = candidate.lower().split(".")[-1]
        return normalized in {"report_date", "work_date", "plan_date"}

    def _biz_month_date_format(self, query_plan: QueryPlan) -> str:
        compact_month_values = [
            str(item.value)
            for item in query_plan.filters
            if item.field == "biz_month"
            and isinstance(item.value, str)
        ]
        if any(re.fullmatch(r"20\d{4}", value) for value in compact_month_values):
            return "%Y%m"
        return "%Y-%m"

    def _has_latest_n_filter(self, query_plan: QueryPlan) -> bool:
        return any(item.op == "latest_n" for item in query_plan.filters)

    def _qualify_columns(self, query_plan: QueryPlan, columns: list[str]) -> list[str]:
        if self.semantic_runtime is None:
            return []
        qualified: list[str] = []
        for column in columns:
            for table_name in query_plan.tables:
                if column in self.semantic_runtime.table_fields(table_name):
                    candidate = f"{table_name}.{column}"
                    if candidate not in qualified:
                        qualified.append(candidate)
        return qualified

    def _domain_tables(self, subject_domain: str) -> list[str] | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.domain_tables(subject_domain)

    def _business_notes(self, subject_domain: str) -> str:
        if not self._business_knowledge or subject_domain == "unknown":
            return ""
        sections: list[str] = []
        total_chars = 0
        for entry in self._business_knowledge:
            domains = {str(item).lower() for item in entry.get("domains", []) if item}
            if subject_domain.lower() not in domains:
                continue
            notes = entry.get("notes", [])
            if not isinstance(notes, list) or not notes:
                continue
            block = "\n".join(f"- {note}" for note in notes if isinstance(note, str) and note.strip())
            if not block:
                continue
            separator_chars = 2 if sections else 0
            projected = total_chars + separator_chars + len(block)
            if projected > self.BUSINESS_NOTES_MAX_CHARS and sections:
                continue
            sections.append(block)
            total_chars = projected
            if total_chars >= self.BUSINESS_NOTES_MAX_CHARS:
                break
        return "\n\n".join(sections)[: self.BUSINESS_NOTES_MAX_CHARS]

    def _supported_domains(self) -> list[str]:
        if self.semantic_runtime is None:
            return []
        return sorted(
            domain_name
            for domain_name in self.semantic_runtime.query_profiles.keys()
            if domain_name != "unknown"
        )

    def _semantic_fields(self, subject_domain: str) -> list[dict]:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return []
        return self.semantic_runtime.semantic_field_metadata(subject_domain=subject_domain)[:20]

    def _classification_evidence(
        self,
        query_intent: QueryIntent,
        session_state: SessionState | None,
        semantic_diff: dict | None,
    ) -> dict:
        semantic_diff = semantic_diff or {}
        return {
            "current_question_signals": {
                "subject_domain": query_intent.subject_domain,
                "matched_metrics": query_intent.matched_metrics,
                "matched_entities": query_intent.matched_entities,
                "filter_fields": [item.field for item in query_intent.filters],
                "time_grain": query_intent.time_context.grain,
                "has_version_context": query_intent.version_context is not None,
                "requested_sort": [item.model_dump() for item in query_intent.requested_sort],
                "requested_limit": query_intent.requested_limit,
                "has_follow_up_cue": query_intent.has_follow_up_cue,
                "has_explicit_slots": query_intent.has_explicit_slots,
            },
            "previous_session_focus": {
                "subject_domain": session_state.subject_domain if session_state is not None else None,
                "metrics": session_state.metrics if session_state is not None else [],
                "entities": session_state.entities if session_state is not None else [],
                "filter_fields": [item.field for item in session_state.filters] if session_state is not None else [],
                "time_grain": session_state.time_context.grain if session_state and session_state.time_context else "unknown",
                "has_version_context": bool(session_state and session_state.version_context is not None),
            },
            "inheritance_targets": {
                "carry_over_metrics": session_state.metrics if session_state is not None else [],
                "carry_over_dimensions": session_state.dimensions if session_state is not None else [],
                "carry_over_filter_fields": [item.field for item in session_state.filters] if session_state is not None else [],
                "carry_over_time_grain": session_state.time_context.grain if session_state and session_state.time_context else "unknown",
                "carry_over_version_field": session_state.version_context.field if session_state and session_state.version_context else None,
            },
            "delta_summary": {
                "domain_changed": semantic_diff.get("domain_changed"),
                "new_metrics": semantic_diff.get("new_metrics", []),
                "new_entities": semantic_diff.get("new_entities", []),
                "new_filter_fields": semantic_diff.get("new_filter_fields", []),
                "reused_filter_fields": semantic_diff.get("reused_filter_fields", []),
                "only_updates_filters": semantic_diff.get("only_updates_filters"),
                "only_updates_time": semantic_diff.get("only_updates_time"),
                "only_updates_version": semantic_diff.get("only_updates_version"),
                "metrics_missing_but_context_resolvable": semantic_diff.get("metrics_missing_but_context_resolvable"),
                "can_execute_without_context": semantic_diff.get("can_execute_without_context"),
                "introduces_new_topic_signal": semantic_diff.get("introduces_new_topic_signal"),
                "is_short_followup_fragment": semantic_diff.get("is_short_followup_fragment"),
            },
        }

    def _classification_delta_examples(self) -> list[dict]:
        return self._prompt_asset_list("classification", "context_delta_examples")

    def _classification_business_examples(self) -> list[dict]:
        return self._prompt_asset_list("classification", "business_few_shots")

    def _prompt_assets(self) -> dict:
        if self.semantic_runtime is None:
            return {}
        payload = self.semantic_runtime.domain_config.get("prompt_assets", {})
        return payload if isinstance(payload, dict) else {}

    def _prompt_asset_list(self, section: str, key: str) -> list[dict]:
        section_payload = self._prompt_assets().get(section, {})
        if not isinstance(section_payload, dict):
            return []
        values = section_payload.get(key, [])
        return values if isinstance(values, list) else []

    def _prompt_asset_strings(self, section: str, key: str) -> list[str]:
        values = self._prompt_asset_value(section, key)
        return [str(item) for item in values] if isinstance(values, list) else []

    def _prompt_asset_dict(self, section: str, key: str) -> dict:
        values = self._prompt_asset_value(section, key)
        return values if isinstance(values, dict) else {}

    def _prompt_asset_value(self, section: str, key: str):
        section_payload = self._prompt_assets().get(section, {})
        if not isinstance(section_payload, dict):
            return None
        return section_payload.get(key)
