from __future__ import annotations

import json

from backend.app.config import README_TEXT_PATH, TABLES_METADATA_PATH
from backend.app.models.classification import SemanticParse
from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class PromptBuilder:
    BUSINESS_NOTES_MAX_CHARS = 2400

    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime
        self._tables_metadata = self._load_tables_metadata()
        self._business_notes = self._load_business_notes()
        self._business_note_chunks = self._split_business_notes(self._business_notes)

    def build_query_plan_prompt(
        self,
        question: str,
        semantic_parse: SemanticParse,
        retrieval: RetrievalContext,
        base_plan: QueryPlan | None = None,
        session_state: SessionState | None = None,
    ) -> dict:
        profile = self._query_profile(semantic_parse.subject_domain)
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
            "query_profile": profile,
            "domain_tables": self._domain_tables(semantic_parse.subject_domain),
            "allowed_semantic_views": self._allowed_semantic_views(semantic_parse.subject_domain),
            "semantic_view_schemas": self._semantic_view_schemas(retrieval.semantic_views),
            "base_plan": base_plan.model_dump() if base_plan is not None else None,
            "allowed_fields": sorted(self._allowed_fields(base_plan)) if base_plan is not None else [],
            "session_state": session_state.model_dump() if session_state is not None else None,
            "instructions": {
                "return_format": "json",
                "constraints": [
                    "prefer real physical tables over semantic views",
                    "semantic views are auxiliary hints only and may not exist in the database",
                    "only use registered domains, real tables, metrics and fields",
                    "respect base_plan and only refine filters, dimensions, sort, version_context and limit when needed",
                    "do not invent new metrics, fields or tables outside the allowed lists",
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
        candidate_scores: dict[str, float] | None = None,
        arbitration_context: dict | None = None,
    ) -> dict:
        evidence = self._classification_evidence(
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
        )
        return {
            "task": "question_classification",
            "question": question,
            "semantic_parse": semantic_parse.model_dump(),
            "session_state": session_state.model_dump() if session_state is not None else None,
            "session_semantic_diff": semantic_diff,
            "classification_evidence": evidence,
            "base_classification": base_classification,
            "allowed_question_types": allowed_question_types,
            "candidate_scores": candidate_scores or {},
            "arbitration_context": arbitration_context or {},
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
                    "context_delta",
                ],
                "category_definitions": {
                    "follow_up": "The current question should inherit the prior session context to execute correctly, and mainly refines or extends the prior analysis.",
                    "new_related": "The current question stays in the same business domain but is independently executable without inheriting prior context.",
                    "new_unrelated": "The current question switches to a different business topic and should not inherit prior context.",
                    "clarification_needed": "The current question still lacks enough information for stable execution or context inheritance judgment.",
                },
                "context_delta_field_guide": {
                    "add_filters": "Use when the current question adds new filter conditions while keeping the prior topic.",
                    "remove_filters": "Use when the current question replaces prior filters from the same filter group, such as time or version fields.",
                    "clear_filters": "Use only when the user explicitly wants to drop prior filters broadly.",
                    "replace_entities": "Use when the referenced business entity changes within the same topic.",
                    "replace_metrics": "Use when the user changes the metric, such as switching from plan input to actual output.",
                    "replace_dimensions": "Use when the user changes the grouping dimension, such as switching to customer split.",
                    "replace_sort": "Use when the user explicitly changes sort order or ranking preference.",
                    "replace_time_context": "Use when the user changes only the time scope or time grain.",
                    "replace_version_context": "Use when the user changes only the version scope.",
                    "replace_limit": "Use when the user changes only the number of rows to return.",
                },
                "context_delta_rules": [
                    "Prefer the smallest valid context_delta instead of rewriting the whole state.",
                    "If the user only changes time, populate replace_time_context and avoid changing metrics or dimensions.",
                    "If the user only changes version, populate replace_version_context and avoid changing unrelated fields.",
                    "If the user changes the metric within the same topic, use replace_metrics.",
                    "If the user changes the grouping such as '按客户拆分', use replace_dimensions rather than add_filters.",
                    "If the question_type is not follow_up, return an empty context_delta.",
                ],
                "context_delta_examples": self._classification_delta_examples(),
                "arbitration_checklist": [
                    "First decide whether the current question truly requires prior session context to execute correctly.",
                    "Then decide whether the question is independently executable inside the same domain.",
                    "If the question stays in the same topic but only changes metric, time, version, grouping or filters, prefer follow_up with a minimal context_delta.",
                    "If the current question switches business topic, prefer new_unrelated and do not inherit context.",
                    "If the local top two candidates are close, explicitly justify why the winning candidate is more coherent than the runner-up.",
                ],
                "business_few_shots": self._classification_business_examples(),
                "constraints": [
                    "choose question_type only from allowed_question_types",
                    "respect structured semantic_parse and session_semantic_diff",
                    "treat candidate_scores as local evidence to arbitrate rather than recompute from scratch",
                    "use classification_evidence and arbitration_context to explain why the top candidate wins over the closest alternative",
                    "if the question is a follow-up refinement, keep inherit_context true",
                    "if question_type is follow_up, return a context_delta that describes what should be inherited, replaced, added or removed",
                    "if question_type is not follow_up, keep context_delta empty",
                    "if the question is independently executable, prefer not to inherit context",
                    "if classification is clarification_needed, provide clarification_question",
                ],
            },
        }

    def build_sql_prompt(self, query_plan: QueryPlan) -> dict:
        selected_sources = query_plan.tables or self._domain_tables(query_plan.subject_domain) or []
        source_schemas = {
            table_name: self._tables_metadata.get(table_name, {})
            for table_name in selected_sources
            if table_name in self._tables_metadata
        }
        sql_preferences = [
            "Use query_plan tables as the primary source of truth for actual database objects.",
            "Use query_plan dimensions, filters, sort and limit exactly unless they would produce invalid SQL.",
        ]
        few_shot = None
        if self._is_demand_plan(query_plan, selected_sources):
            sql_preferences = [
                "For p_demand/v_demand horizontal demand tables, target demand month is computed from base MONTH plus offset: offset 0 REQUIREMENT_QTY, offset 1 NEXT_REQUIREMENT, offset 2 LAST_REQUIREMENT, offset 3 MONTH4, offset 4 MONTH5, offset 5 MONTH6, offset 6 MONTH7.",
                "If query_plan filters contain demand_month='YYYYMM', build a CTE with columns PM_VERSION, FGCODE, customer dimensions, demand_month, demand_qty, then filter computed demand_month = 'YYYYMM'. Do not compare the base MONTH to future months as a CASE shortcut.",
                "When query_plan filters contain PM_VERSION with op latest_n, compute latest distinct versions using SELECT PM_VERSION FROM <source table> GROUP BY PM_VERSION ORDER BY PM_VERSION DESC LIMIT count.",
                *sql_preferences,
            ]
            few_shot = {
                "question_pattern": "最新N版P版需求中，YYYYMM需求最多的FGCODE是哪一个",
                "sql_shape": [
                    "WITH latest_versions AS (SELECT PM_VERSION FROM p_demand GROUP BY PM_VERSION ORDER BY PM_VERSION DESC LIMIT N)",
                    "demand_unpivot AS (UNION ALL rows where demand_month is MONTH plus each offset field)",
                    "SELECT FGCODE, SUM(demand_qty) AS demand_qty FROM demand_unpivot WHERE demand_month='YYYYMM' AND PM_VERSION IN (...) GROUP BY FGCODE ORDER BY demand_qty DESC LIMIT 1",
                ],
            }
        business_notes = self._business_notes_for_plan(query_plan, selected_sources)
        context_budget = {
            "business_notes_max_chars": self.BUSINESS_NOTES_MAX_CHARS,
            "business_notes_mode": "ranked_relevant_chunks",
            "tables_metadata_mode": "selected_query_plan_tables_only",
        }
        context_summary = {
            "selected_sources": selected_sources,
            "tables_metadata_count": len(source_schemas),
            "business_notes_chars": len(business_notes),
            "few_shot_used": few_shot is not None,
            "subject_domain": query_plan.subject_domain,
        }
        return {
            "task": "sql_generation",
            "query_plan": query_plan.model_dump(),
            "allowed_sources": selected_sources,
            "allowed_fields": sorted(self._allowed_fields(query_plan)),
            "tables_metadata": source_schemas,
            "business_notes": business_notes,
            "context_budget": context_budget,
            "context_summary": context_summary,
            "instructions": {
                "return_format": "sql_only",
                "constraints": [
                    "generate MySQL readonly SQL using real physical tables first",
                    "prefer WITH CTE over referencing semantic views that may not exist in the database",
                    "only use real tables from tables_metadata",
                    "do not reference semantic_demand_unpivot_view or other semantic views unless explicitly requested",
                    "must include LIMIT",
                    "if demand tables are horizontal, expand them with a CTE when needed",
                    "do not use SELECT *",
                    "aggregate metrics with GROUP BY all query_plan dimensions",
                    "only return SQL, no markdown or explanation",
                ],
                "sql_preferences": sql_preferences,
                "few_shot": few_shot,
            },
        }

    def _load_tables_metadata(self) -> dict:
        try:
            return json.loads(TABLES_METADATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_business_notes(self) -> str:
        try:
            return README_TEXT_PATH.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _split_business_notes(self, notes: str) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        for raw_line in notes.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    chunks.append("\n".join(current))
                    current = []
                continue
            if current and self._starts_new_note_chunk(line):
                chunks.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _starts_new_note_chunk(self, line: str) -> bool:
        return bool(line[:2] and line[0].isdigit() and line[1] in {".", "、", ")"})

    def _business_notes_for_plan(self, query_plan: QueryPlan, selected_sources: list[str] | None) -> str:
        if not self._business_note_chunks:
            return ""
        terms = self._business_note_terms(query_plan, selected_sources)
        scored_chunks: list[tuple[int, int, str]] = []
        for index, chunk in enumerate(self._business_note_chunks):
            lower_chunk = chunk.lower()
            score = sum(1 for term in terms if term and term in lower_chunk)
            if score:
                scored_chunks.append((score, -index, chunk))
        if not scored_chunks:
            scored_chunks = [(0, -index, chunk) for index, chunk in enumerate(self._business_note_chunks[:2])]
        selected_chunks: list[str] = []
        total_chars = 0
        for _score, _negative_index, chunk in sorted(scored_chunks, reverse=True):
            separator_chars = 2 if selected_chunks else 0
            projected = total_chars + separator_chars + len(chunk)
            if projected > self.BUSINESS_NOTES_MAX_CHARS and selected_chunks:
                continue
            selected_chunks.append(chunk)
            total_chars = projected
            if total_chars >= self.BUSINESS_NOTES_MAX_CHARS:
                break
        notes = "\n\n".join(selected_chunks)
        return notes[: self.BUSINESS_NOTES_MAX_CHARS]

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

    def _is_demand_plan(self, query_plan: QueryPlan, selected_sources: list[str] | None) -> bool:
        sources = set(selected_sources or []) | set(query_plan.tables)
        return (
            query_plan.subject_domain == "demand"
            or bool({"p_demand", "v_demand"}.intersection(sources))
            or any(metric.startswith("demand") for metric in query_plan.metrics)
        )

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


    def _allowed_semantic_views(self, subject_domain: str) -> list[str] | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.semantic_views_for_domain(subject_domain)

    def _semantic_view_schemas(self, semantic_views: list[str]) -> dict[str, list[str]]:
        if self.semantic_runtime is None:
            return {}
        return {
            view_name: self.semantic_runtime.semantic_view_fields(view_name)
            for view_name in semantic_views
        }

    def _classification_evidence(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
        semantic_diff: dict | None,
    ) -> dict:
        semantic_diff = semantic_diff or {}
        return {
            "current_question_signals": {
                "subject_domain": semantic_parse.subject_domain,
                "matched_metrics": semantic_parse.matched_metrics,
                "matched_entities": semantic_parse.matched_entities,
                "filter_fields": [item.field for item in semantic_parse.filters],
                "time_grain": semantic_parse.time_context.grain,
                "has_version_context": semantic_parse.version_context is not None,
                "requested_sort": [item.model_dump() for item in semantic_parse.requested_sort],
                "requested_limit": semantic_parse.requested_limit,
                "has_follow_up_cue": semantic_parse.has_follow_up_cue,
                "has_explicit_slots": semantic_parse.has_explicit_slots,
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
        return [
            {
                "question": "换成上个月",
                "question_type": "follow_up",
                "context_delta": {
                    "replace_time_context": {
                        "grain": "month",
                        "range": {"start": "relative:last_month", "end": "relative:last_month"},
                    }
                },
            },
            {
                "question": "那实际产出呢",
                "question_type": "follow_up",
                "context_delta": {
                    "replace_metrics": ["actual_output_qty"],
                },
            },
            {
                "question": "按客户拆分",
                "question_type": "follow_up",
                "context_delta": {
                    "replace_dimensions": ["customer"],
                },
            },
            {
                "question": "只看 TV 类产品",
                "question_type": "follow_up",
                "context_delta": {
                    "add_filters": [
                        {"field": "application", "op": "=", "value": "TV"},
                    ],
                },
            },
            {
                "question": "查询2026年4月库存",
                "question_type": "new_unrelated",
                "context_delta": {},
            },
        ]

    def _classification_business_examples(self) -> list[dict]:
        return [
            {
                "previous_session": {
                    "subject_domain": "plan_actual",
                    "metrics": ["plan_input_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory", "op": "=", "value": "CELL"}],
                },
                "question": "那实际产出呢",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The topic stays in plan_actual and the user is switching the metric while reusing the same analysis frame.",
                    "context_delta": {"replace_metrics": ["actual_output_qty"]},
                },
            },
            {
                "previous_session": {
                    "subject_domain": "plan_actual",
                    "metrics": ["plan_input_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory", "op": "=", "value": "CELL"}],
                },
                "question": "按客户拆分",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The topic remains the same and the user is changing the grouping dimension rather than starting a new request.",
                    "context_delta": {"replace_dimensions": ["customer"]},
                },
            },
            {
                "previous_session": {
                    "subject_domain": "plan_actual",
                    "metrics": ["plan_input_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory", "op": "=", "value": "CELL"}],
                },
                "question": "查询2026年4月库存",
                "expected": {
                    "question_type": "new_unrelated",
                    "inherit_context": False,
                    "reason": "The user switches from plan_actual analysis to inventory analysis, so prior context should not be inherited.",
                    "context_delta": {},
                },
            },
            {
                "previous_session": {
                    "subject_domain": "inventory",
                    "metrics": ["inventory_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory_code", "op": "=", "value": "C1_CELL"}],
                },
                "question": "换成上个月",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The user keeps the same inventory topic and only changes the time scope.",
                    "context_delta": {
                        "replace_time_context": {
                            "grain": "month",
                            "range": {"start": "relative:last_month", "end": "relative:last_month"},
                        }
                    },
                },
            },
            {
                "previous_session": {
                    "subject_domain": "demand",
                    "metrics": ["demand_qty"],
                    "dimensions": ["report_month"],
                    "filters": [{"field": "PM_VERSION", "op": "=", "value": "V1"}],
                },
                "question": "改成V2版本",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The topic remains demand analysis and the user is only switching the version scope.",
                    "context_delta": {
                        "remove_filters": ["PM_VERSION"],
                        "replace_version_context": {"field": "PM_VERSION", "value": "V2"},
                    },
                },
            },
            {
                "previous_session": {
                    "subject_domain": "plan_actual",
                    "metrics": ["plan_input_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory", "op": "=", "value": "CELL"}],
                },
                "question": "只看ARRAY工厂",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The user keeps the same production topic and only replaces the factory filter.",
                    "context_delta": {
                        "remove_filters": ["factory"],
                        "add_filters": [{"field": "factory", "op": "=", "value": "ARRAY"}],
                    },
                },
            },
            {
                "previous_session": {
                    "subject_domain": "inventory",
                    "metrics": ["inventory_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory_code", "op": "=", "value": "C1_CELL"}],
                },
                "question": "不要工厂条件了",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The topic stays the same and the user explicitly asks to drop the prior factory filter.",
                    "context_delta": {
                        "remove_filters": ["factory_code"],
                    },
                },
            },
            {
                "previous_session": {
                    "subject_domain": "plan_actual",
                    "metrics": ["actual_output_qty"],
                    "dimensions": ["biz_month"],
                    "filters": [{"field": "factory", "op": "=", "value": "CELL"}],
                },
                "question": "按天展开",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The user keeps the same topic and changes the analysis granularity from summary to daily breakdown.",
                    "context_delta": {
                        "replace_dimensions": ["biz_date"],
                        "replace_time_context": {
                            "grain": "day",
                            "range": {"start": None, "end": None},
                        },
                    },
                },
            },
            {
                "previous_session": {
                    "subject_domain": "sales_financial",
                    "metrics": ["sales_qty"],
                    "dimensions": ["customer"],
                    "filters": [],
                },
                "question": "按销售量降序，只看前10条",
                "expected": {
                    "question_type": "follow_up",
                    "inherit_context": True,
                    "reason": "The topic stays the same and the user only changes ranking preference and result size.",
                    "context_delta": {
                        "replace_sort": [{"field": "sales_qty", "order": "desc"}],
                        "replace_limit": 10,
                    },
                },
            },
        ]
