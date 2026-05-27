from __future__ import annotations

import re

from backend.app.models.classification import QueryIntent
from backend.app.models.example_library import ExampleRecord
from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_dialect import SqlDialect


class PromptBuilder:
    BUSINESS_NOTES_MAX_CHARS = 2400

    def __init__(
        self,
        semantic_runtime: SemanticRuntime | None = None,
        metadata_registry: MetadataRegistry | None = None,
    ) -> None:
        self.semantic_runtime = semantic_runtime
        self.metadata_registry = metadata_registry or MetadataRegistry()
        self.sql_dialect = SqlDialect.from_name("oracle")

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
        time_resolution = self._time_resolution(query_plan)
        shape_contract = self._shape_contract(query_plan, time_resolution=time_resolution)
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
                *self._latest_n_preferences(),
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
            "time_resolution_count": len(time_resolution),
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
            "oracle_sql_rules": {
                "name": self.sql_dialect.name,
                "label": self.sql_dialect.label,
                "result_limit_clause": self.sql_dialect.result_limit_clause_name,
            },
            "query_plan": query_plan.model_dump(),
            "allowed_sources": selected_sources,
            "allowed_fields": sorted(self._sql_allowed_fields(query_plan)),
            "field_resolution": field_resolution,
            "time_resolution": time_resolution,
            "shape_contract": shape_contract,
            "tables_metadata": source_schemas,
            "business_notes": business_notes,
            "join_patterns": self._selected_join_patterns(retrieval),
            "context_budget": context_budget,
            "context_summary": context_summary,
            "instructions": {
                "return_format": "sql_only",
                "constraints": self._sql_generation_constraints(),
                "sql_preferences": sql_preferences,
                "few_shot": {
                    "retrieved_examples": retrieved_examples,
                },
            },
        }

    def _load_tables_metadata(self) -> dict:
        return self.metadata_registry.tables_metadata

    def _load_examples(self) -> dict[str, ExampleRecord]:
        payload = self.metadata_registry.examples_template
        examples: dict[str, ExampleRecord] = {}
        for index, item in enumerate(payload if isinstance(payload, list) else []):
            try:
                example = ExampleRecord(**item)
            except Exception as exc:
                raise RuntimeError(f"invalid example record at index {index}: {exc}") from exc
            examples[example.id] = example
        return examples

    def _load_business_knowledge(self) -> list[dict]:
        return self.metadata_registry.business_knowledge_entries

    @property
    def _tables_metadata(self) -> dict:
        return self._load_tables_metadata()

    @property
    def _business_knowledge(self) -> list[dict]:
        return self._load_business_knowledge()

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
            payload = {
                "id": example.id,
                "question": example.question,
                "intent": example.intent,
                "tables": example.tables,
                "metrics": example.metrics,
                "dimensions": example.dimensions,
                "filters": [item.model_dump(mode="json") for item in example.filters],
                "result_shape": example.result_shape,
                "notes": example.notes,
                "matched_features": hit.matched_features,
            }
            payload["sql_omitted_reason"] = "reuse only semantic shape, tables, metrics, filters, and result_shape"
            selected.append(payload)
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

    def _shape_contract(self, query_plan: QueryPlan, time_resolution: dict | None = None) -> dict:
        required_projection = list(query_plan.dimensions)
        aggregate_metrics = list(query_plan.metrics)
        dimension_hints: list[str] = []
        logical_dimension_examples: dict[str, list[str]] = {}
        time_resolution = time_resolution or {}
        for field in required_projection:
            examples = self._logical_dimension_examples(field, time_resolution)
            if examples:
                logical_dimension_examples[field] = examples
                if field == "biz_month":
                    dimension_hints.append(
                        "如果需要把逻辑月份 biz_month 映射到真实字段，优先参考 time_resolution.biz_month.candidates 里的 projection_example，并保持最终外层 SELECT 与 GROUP BY 使用同一表达式或别名。"
                    )
        return {
            "required_projection": required_projection,
            "required_group_by": required_projection if aggregate_metrics else [],
            "aggregate_metrics": aggregate_metrics,
            "logical_dimension_examples": logical_dimension_examples,
            "dimension_hints": dimension_hints,
        }

    def _logical_dimension_examples(self, logical_field: str, time_resolution: dict) -> list[str]:
        candidates = time_resolution.get(logical_field, {}).get("candidates", [])
        examples: list[str] = []
        for candidate in candidates:
            example = candidate.get("projection_example")
            if isinstance(example, str) and example and example not in examples:
                examples.append(example)
        return examples

    def _time_resolution(self, query_plan: QueryPlan) -> dict:
        if self.semantic_runtime is None:
            return {}
        resolution: dict[str, dict] = {}
        for logical_field in ["biz_date", "biz_month"]:
            candidates = self._time_resolution_candidates(query_plan, logical_field)
            if candidates:
                resolution[logical_field] = {
                    "candidates": candidates,
                }
        return resolution

    def _time_resolution_candidates(self, query_plan: QueryPlan, logical_field: str) -> list[dict]:
        if self.semantic_runtime is None:
            return []
        if logical_field == "biz_date":
            candidates: list[dict] = []
            for candidate in self.semantic_runtime.resolve_time_field_candidates(
                query_plan.subject_domain,
                query_plan.tables,
                logical_field,
            ):
                field_expr = candidate["qualified_field"]
                payload = {
                    "field": field_expr,
                    "grain": candidate.get("grain"),
                    "format": candidate.get("format"),
                }
                day_filter_example = self._day_filter_example(query_plan, field_expr, candidate.get("format"))
                if day_filter_example:
                    payload["day_filter_example"] = day_filter_example
                month_range_example = self._month_range_filter_example(
                    query_plan,
                    field_expr,
                    candidate.get("format"),
                )
                if month_range_example:
                    payload["month_range_filter_example"] = month_range_example
                candidates.append(payload)
            return candidates

        if logical_field != "biz_month":
            return []

        candidates = []
        sample_month = self._example_compact_month(query_plan)
        for candidate in self.semantic_runtime.resolve_time_field_candidates(
            query_plan.subject_domain,
            query_plan.tables,
            logical_field,
        ):
            field_expr = candidate["qualified_field"]
            projection_expr = self._month_projection_expression(field_expr, candidate.get("format")) or field_expr
            payload = {
                "field": field_expr,
                "grain": candidate.get("grain"),
                "format": candidate.get("format"),
                "projection_example": f"{projection_expr} AS biz_month",
            }
            if sample_month:
                month_literal = self.semantic_runtime.format_time_literal(sample_month, candidate.get("format")) or sample_month
                payload["month_filter_example"] = f"{field_expr} = '{month_literal}'"
            candidates.append(payload)

        for candidate in self.semantic_runtime.resolve_time_field_candidates(
            query_plan.subject_domain,
            query_plan.tables,
            "biz_date",
        ):
            field_expr = candidate["qualified_field"]
            projection_expr = self._month_projection_expression(field_expr, candidate.get("format"))
            if not projection_expr:
                continue
            payload = {
                "field": field_expr,
                "grain": candidate.get("grain"),
                "format": candidate.get("format"),
                "projection_example": f"{projection_expr} AS biz_month",
            }
            if sample_month:
                payload["month_filter_example"] = f"{projection_expr} = '{sample_month}'"
            month_range_example = self._month_range_filter_example(
                query_plan,
                field_expr,
                candidate.get("format"),
            )
            if month_range_example:
                payload["month_range_filter_example"] = month_range_example
            candidates.append(payload)
        return candidates

    def _month_projection_expression(self, field_expression: str, field_format: str | None) -> str | None:
        normalized_format = str(field_format or "").strip().upper()
        if normalized_format == "YYYYMM":
            return field_expression
        if normalized_format == "YYYY-MM":
            return f"REPLACE({field_expression}, '-', '')"
        if normalized_format == "YYYYMMDD":
            return f"{self._substring_function()}({field_expression}, 1, 6)"
        if normalized_format == "YYYY-MM-DD":
            return f"REPLACE({self._substring_function()}({field_expression}, 1, 7), '-', '')"
        return None

    def _substring_function(self) -> str:
        return "SUBSTR"

    def _example_compact_month(self, query_plan: QueryPlan) -> str:
        if self.semantic_runtime is not None:
            for item in query_plan.filters:
                if item.field not in {"biz_month", "demand_month"}:
                    continue
                candidate_values = [item.value]
                if item.op == "between" and isinstance(item.value, list):
                    candidate_values = list(item.value)
                for candidate_value in candidate_values:
                    compact_month = self.semantic_runtime.compact_month_value(str(candidate_value))
                    if compact_month:
                        return compact_month
            time_context = query_plan.time_context
            if time_context and time_context.range:
                for candidate_value in [time_context.range.start, time_context.range.end]:
                    compact_month = self.semantic_runtime.compact_month_value(candidate_value)
                    if compact_month:
                        return compact_month
        return "202604"

    def _example_iso_day(self, query_plan: QueryPlan) -> str:
        if self.semantic_runtime is not None:
            for item in query_plan.filters:
                if item.field != "biz_date":
                    continue
                candidate_values = [item.value]
                if item.op == "between" and isinstance(item.value, list):
                    candidate_values = list(reversed(item.value))
                for candidate_value in candidate_values:
                    iso_day = self.semantic_runtime.format_time_literal(str(candidate_value), "YYYY-MM-DD")
                    if iso_day:
                        return iso_day
            time_context = query_plan.time_context
            if time_context and time_context.range:
                for candidate_value in [time_context.range.end, time_context.range.start]:
                    iso_day = self.semantic_runtime.format_time_literal(candidate_value, "YYYY-MM-DD")
                    if iso_day:
                        return iso_day
            sample_month = self._example_compact_month(query_plan)
            month_range = self.semantic_runtime.month_range_literals(sample_month, "YYYY-MM-DD")
            if month_range:
                return month_range[1]
        return "2026-04-30"

    def _day_filter_example(
        self,
        query_plan: QueryPlan,
        field_expression: str,
        field_format: str | None,
    ) -> str | None:
        if self.semantic_runtime is None:
            return None
        iso_day = self._example_iso_day(query_plan)
        literal = self.semantic_runtime.format_time_literal(iso_day, field_format)
        if not literal:
            return None
        return f"{field_expression} = '{literal}'"

    def _month_range_filter_example(
        self,
        query_plan: QueryPlan,
        field_expression: str,
        field_format: str | None,
    ) -> str | None:
        if self.semantic_runtime is None:
            return None
        compact_month = self._example_compact_month(query_plan)
        literals = self.semantic_runtime.month_range_literals(compact_month, field_format)
        if not literals:
            return None
        start_literal, end_literal = literals
        return f"{field_expression} BETWEEN '{start_literal}' AND '{end_literal}'"

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

    def _sql_generation_constraints(self) -> list[str]:
        constraints = []
        for item in self._prompt_asset_strings("sql_generation", "base_constraints"):
            if "MySQL" in item:
                constraints.append(f"优先基于真实物理表生成 {self.sql_dialect.label} 只读查询。")
                continue
            if "必须包含 LIMIT" in item:
                constraints.append(f"必须包含结果行数限制，并使用 {self.sql_dialect.result_limit_clause_name} 语法。")
                continue
            constraints.append(item)
        constraints.extend(
            [
                "不要使用 MySQL 专属语法，例如 LIMIT、DATE_FORMAT、STR_TO_DATE、DATE_ADD、CURDATE、反引号。",
                "Oracle 日期函数优先使用 TO_DATE、TO_CHAR、ADD_MONTHS、TRUNC、SYSDATE。",
            ]
        )
        return constraints

    def _latest_n_preferences(self) -> list[str]:
        preferences = []
        for item in self._prompt_asset_strings("sql_generation", "latest_n_preferences"):
            if "ORDER BY 真实排序字段 DESC LIMIT N" in item:
                preferences.append(
                    "当 latest_n.count = 1 时，优先使用 MAX(真实排序字段) 形成单值过滤；当 latest_n.count > 1 时，可使用子查询 ORDER BY 真实排序字段 DESC FETCH FIRST N ROWS ONLY。"
                )
                continue
            preferences.append(item)
        return preferences
