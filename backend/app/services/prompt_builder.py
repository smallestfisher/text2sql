from __future__ import annotations

import json

from backend.app.config import BUSINESS_KNOWLEDGE_PATH, TABLES_METADATA_PATH
from backend.app.models.classification import QueryIntent
from backend.app.models.query_plan import QueryPlan
from backend.app.models.retrieval import RetrievalContext
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class PromptBuilder:
    BUSINESS_NOTES_MAX_CHARS = 2400

    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime
        self._tables_metadata = self._load_tables_metadata()
        self._business_knowledge = self._load_business_knowledge()

    def build_query_plan_prompt(
        self,
        question: str,
        query_intent: QueryIntent,
        retrieval: RetrievalContext,
        base_plan: QueryPlan | None = None,
        session_state: SessionState | None = None,
    ) -> dict:
        profile = self._query_profile(query_intent.subject_domain)
        return {
            "task": "query_plan_generation",
            "question": question,
            "subject_domain": query_intent.subject_domain,
            "metrics": query_intent.matched_metrics,
            "entities": query_intent.matched_entities,
            "session_semantic_diff": self._session_semantic_diff(query_intent, session_state),
            "retrieval_terms": retrieval.retrieval_terms,
            "retrieval_hits": [hit.model_dump() for hit in retrieval.hits],
            "query_profile": profile,
            "domain_tables": self._domain_tables(query_intent.subject_domain),
            "base_plan": base_plan.model_dump() if base_plan is not None else None,
            "allowed_fields": sorted(self._allowed_fields(base_plan)) if base_plan is not None else [],
            "session_state": session_state.model_dump() if session_state is not None else None,
            "instructions": {
                "return_format": "json",
                "constraints": [
                    "优先使用真实物理表，不要优先依赖数据库预建分析对象。",
                    "数据库预建分析对象只作为辅助提示，真实数据库里可能并不存在。",
                    "只能使用系统已登记的 domain、真实表、指标和字段。",
                    "尊重 base_plan，只在确有必要时微调 filters、dimensions、sort、version_context 和 limit。",
                    "不要发明允许列表之外的新指标、新字段或新表。",
                    "如果是追问，且当前问题只是在细化筛选或时间范围，应保留之前的主题。",
                ],
                "fields": [
                    "subject_domain",
                    "tables",
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
                    "follow_up": "当前问题需要继承上一个会话上下文才能正确执行，本质上是在细化或延续之前的分析。",
                    "new_related": "当前问题仍属于同一个业务域，但即使不继承上文也可以独立执行。",
                    "new_unrelated": "当前问题切换到了新的业务主题，不应继承上文。",
                    "clarification_needed": "当前问题信息仍然不足，暂时无法稳定判断执行方式或是否继承上下文。",
                },
                "context_delta_field_guide": {
                    "add_filters": "当当前问题是在延续原主题，并新增筛选条件时使用。",
                    "remove_filters": "当当前问题要替换同一类旧筛选时使用，例如时间或版本条件。",
                    "clear_filters": "只有在用户明确要求大范围移除旧筛选时才使用。",
                    "replace_entities": "当同一主题下关注的业务实体发生变化时使用。",
                    "replace_metrics": "当用户切换指标时使用，例如从计划投入切换到实际产出。",
                    "replace_dimensions": "当用户切换分组维度时使用，例如改为按客户拆分。",
                    "replace_sort": "当用户明确修改排序方式或排名偏好时使用。",
                    "replace_time_context": "当用户只修改时间范围或时间粒度时使用。",
                    "replace_version_context": "当用户只修改版本范围时使用。",
                    "replace_limit": "当用户只修改返回条数时使用。",
                },
                "context_delta_rules": [
                    "优先返回最小可行的 context_delta，不要重写整个状态。",
                    "如果用户只改时间，就填写 replace_time_context，不要顺带改 metrics 或 dimensions。",
                    "如果用户只改版本，就填写 replace_version_context，不要改无关字段。",
                    "如果用户在同一主题下切换指标，使用 replace_metrics。",
                    "如果用户调整分组方式，例如“按客户拆分”，应使用 replace_dimensions，而不是 add_filters。",
                    "如果 question_type 不是 follow_up，返回空的 context_delta。",
                ],
                "context_delta_examples": self._classification_delta_examples(),
                "arbitration_checklist": [
                    "先判断当前问题是否真的需要依赖上一轮上下文才能正确执行。",
                    "再判断这个问题在同一业务域内是否可以独立执行。",
                    "如果问题主题没变，只是改了指标、时间、版本、分组或筛选，优先选择 follow_up，并给出最小 context_delta。",
                    "如果当前问题切换了业务主题，优先选择 new_unrelated，并且不要继承上下文。",
                    "如果本地前两名候选很接近，要明确说明为什么第一名比第二名更合理。",
                ],
                "business_few_shots": self._classification_business_examples(),
                "constraints": [
                    "question_type 只能从 allowed_question_types 中选择。",
                    "必须尊重结构化的 query_intent 和 session_semantic_diff。",
                    "把 candidate_scores 当作本地证据做裁决，不要完全推翻后从零重算。",
                    "结合 classification_evidence 和 arbitration_context，解释为什么最终候选优于最接近的备选。",
                    "如果问题是对上一轮的细化追问，保持 inherit_context=true。",
                    "如果 question_type 是 follow_up，返回 context_delta，说明哪些内容需要继承、替换、新增或删除。",
                    "如果 question_type 不是 follow_up，保持 context_delta 为空。",
                    "如果问题本身可以独立执行，优先不要继承上下文。",
                    "如果 classification 是 clarification_needed，提供 clarification_question。",
                ],
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
                "in_scope_examples": [
                    "查 202604 的需求最多的 FGCODE",
                    "继续，只看上个月库存",
                    "按客户看销售业绩 top 10",
                ],
                "out_of_scope_examples": [
                    "你好",
                    "今天天气怎么样",
                    "写一首诗",
                    "你是谁",
                ],
            },
            "instructions": {
                "return_format": "json",
                "fields": ["decision", "confidence", "reason", "suggested_reply"],
                "decision_values": {
                    "business_query": "该输入属于业务 Text2SQL 工作流范围内，即使还需要进一步澄清，也应继续留在流程里。",
                    "out_of_scope": "该输入不属于本系统的业务数据查询或业务追问。",
                    "uncertain": "该输入过于模糊，暂时无法高置信度判定为 out_of_scope。",
                },
                "constraints": [
                    "如果输入是业务数据问题，只是信息不完整，应选择 business_query，而不是 out_of_scope。",
                    "如果当前有会话上下文，且用户是在做简短业务追问，应选择 business_query。",
                    "问候、闲聊、身份提问、天气、创作、翻译和无关请求都应判定为 out_of_scope。",
                    "回复要简洁、可执行。",
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
        field_resolution = self._field_resolution(query_plan)
        sql_preferences = [
            "以 query_plan.tables 为真实数据库对象的首要依据。",
            "严格遵循 query_plan 里的 dimensions、filters、sort 和 limit，除非那样会生成无效 SQL。",
        ]
        few_shot = None
        if self._is_demand_plan(query_plan, selected_sources):
            sql_preferences = [
                "对于 p_demand/v_demand 这类横向需求表，MONTH 是起始需求月份。REQUIREMENT_QTY 对应 base MONTH，NEXT_REQUIREMENT 对应 base MONTH 加 1 个月，LAST_REQUIREMENT 对应 base MONTH 加 2 个月，MONTH4 到 MONTH7 对应 base MONTH 加 3 到 6 个月。",
                "如果 query_plan.filters 包含 demand_month='YYYYMM'，请先构造一个包含 PM_VERSION、FGCODE、客户维度、demand_month、demand_qty 的 CTE，并确保产出的每个 demand_month 都与筛选值保持相同的紧凑 YYYYMM 格式。",
                "如果外层 SQL 还要按 PM_VERSION 过滤，或者要和 latest_versions 做 IN/JOIN 比较，那么 demand_unpivot 的每个 UNION ALL 分支都必须显式产出 PM_VERSION，不能在 CTE 外层引用一个未投影出来的 PM_VERSION。",
                "当 MONTH 在真实表里以紧凑 YYYYMM 编码存储时，不要直接对原始 MONTH 调用 DATE_ADD、ADDDATE、DATE_FORMAT 或类似函数。应先把 MONTH 转成真实日期，再格式化回 YYYYMM；或者对于 REQUIREMENT_QTY 直接保留 base MONTH。",
                "如果目标 demand_month 就是 base MONTH，本月需求应直接把 REQUIREMENT_QTY 映射到 MONTH，不要做日期运算。",
                "如果 query_plan.filters 中 PM_VERSION 的 op 是 latest_n，应先用 SELECT PM_VERSION FROM <source table> GROUP BY PM_VERSION ORDER BY PM_VERSION DESC LIMIT count 计算最新 N 个版本。",
                *sql_preferences,
            ]
            few_shot = {
                "question_pattern": "最新N版P版需求中，YYYYMM需求最多的FGCODE是哪一个",
                "sql_shape": [
                    "WITH latest_versions AS (SELECT PM_VERSION FROM p_demand GROUP BY PM_VERSION ORDER BY PM_VERSION DESC LIMIT N)",
                    "demand_unpivot AS (SELECT PM_VERSION, FGCODE, CAST(MONTH AS CHAR) AS demand_month, REQUIREMENT_QTY AS demand_qty FROM p_demand UNION ALL SELECT PM_VERSION, FGCODE, DATE_FORMAT(DATE_ADD(STR_TO_DATE(CONCAT(CAST(MONTH AS CHAR), '01'), '%Y%m%d'), INTERVAL 1 MONTH), '%Y%m') AS demand_month, NEXT_REQUIREMENT AS demand_qty FROM p_demand ...)",
                    "SELECT FGCODE, SUM(demand_qty) AS demand_qty FROM demand_unpivot WHERE demand_month='YYYYMM' AND PM_VERSION IN (...) GROUP BY FGCODE ORDER BY demand_qty DESC LIMIT 1",
                ],
            }
        business_notes = self._business_notes_for_plan(query_plan, selected_sources)
        business_notes_source = self._business_notes_source_for_plan(query_plan, selected_sources)
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
            "few_shot_used": few_shot is not None,
            "subject_domain": query_plan.subject_domain,
            "business_knowledge_entry_ids": self._selected_business_knowledge_ids(query_plan, selected_sources),
        }
        return {
            "task": "sql_generation",
            "query_plan": query_plan.model_dump(),
            "allowed_sources": selected_sources,
            "allowed_fields": sorted(self._sql_allowed_fields(query_plan)),
            "field_resolution": field_resolution,
            "tables_metadata": source_schemas,
            "business_notes": business_notes,
            "context_budget": context_budget,
            "context_summary": context_summary,
            "instructions": {
                "return_format": "sql_only",
                "constraints": [
                    "优先基于真实物理表生成 MySQL 只读 SQL。",
                    "优先使用 WITH CTE，不要依赖数据库里可能不存在的预建分析对象。",
                    "只能使用 tables_metadata 中出现的真实表。",
                    "query_plan 中的维度、过滤和指标名可能是语义字段，写 SQL 前必须先映射到 tables_metadata 里的真实物理列。",
                    "优先使用 field_resolution 里的 physical_candidates，不要把只存在于 query_plan 中的逻辑字段名直接写进 SQL。",
                    "除非用户明确要求，否则不要引用数据库里预建的展开对象或其他额外分析对象。",
                    "必须包含 LIMIT。",
                    "如果需求表是横表，需要时请先用 CTE 展开。",
                    "不要使用 SELECT *。",
                    "如果有聚合指标，必须按照 query_plan.dimensions 完整 GROUP BY。",
                    "只返回 SQL，不要返回 markdown 或解释。",
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

    def _load_business_knowledge(self) -> list[dict]:
        try:
            payload = json.loads(BUSINESS_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
        entries = payload.get("entries", [])
        return entries if isinstance(entries, list) else []

    def _business_notes_for_plan(self, query_plan: QueryPlan, selected_sources: list[str] | None) -> str:
        return self._structured_business_notes_for_plan(query_plan, selected_sources)

    def _business_notes_source_for_plan(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
    ) -> str:
        if self._select_business_knowledge_entries(query_plan, selected_sources):
            return "structured_knowledge"
        return "none"

    def _structured_business_notes_for_plan(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
    ) -> str:
        selected_entries = self._select_business_knowledge_entries(query_plan, selected_sources)
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
    ) -> list[dict]:
        if not self._business_knowledge:
            return []
        terms = self._business_note_terms(query_plan, selected_sources)
        selected: list[tuple[int, int, dict]] = []
        for index, entry in enumerate(self._business_knowledge):
            score = self._score_business_knowledge_entry(query_plan, selected_sources, terms, entry)
            if score <= 0:
                continue
            selected.append((score, -index, entry))
        selected.sort(reverse=True)
        return [entry for _score, _negative_index, entry in selected]

    def _selected_business_knowledge_ids(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
    ) -> list[str]:
        return [
            str(entry.get("id"))
            for entry in self._select_business_knowledge_entries(query_plan, selected_sources)
            if entry.get("id")
        ]

    def _score_business_knowledge_entry(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str] | None,
        terms: set[str],
        entry: dict,
    ) -> int:
        score = 0
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
        return score

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
        return sorted(item for item in resolved if item in physical_allowed)

    def _physical_metric_candidates(self, query_plan: QueryPlan, metric_name: str) -> list[str]:
        if self.semantic_runtime is None:
            return []
        return sorted(
            self.semantic_runtime.metric_expression_columns(
                metric_name,
                table_names=query_plan.tables,
            )
        )

    def _domain_tables(self, subject_domain: str) -> list[str] | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.domain_tables(subject_domain)

    def _supported_domains(self) -> list[str]:
        if self.semantic_runtime is None:
            return []
        return sorted(
            domain_name
            for domain_name in self.semantic_runtime.query_profiles.keys()
            if domain_name != "unknown"
        )

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
                    "reason": "主题仍然是 plan_actual，用户只是沿用同一分析框架切换指标。",
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
                    "reason": "主题没有变化，用户只是修改分组维度，并不是发起一个全新的请求。",
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
                    "reason": "用户从 plan_actual 分析切换到了 inventory 分析，因此不应继承之前的上下文。",
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
                    "reason": "用户保持同一个 inventory 主题，只修改了时间范围。",
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
                    "reason": "主题仍然是 demand 分析，用户只是切换了版本范围。",
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
                    "reason": "用户保持同一个生产主题，只是替换了工厂筛选条件。",
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
                    "reason": "主题保持不变，且用户明确要求去掉之前的工厂筛选。",
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
                    "reason": "用户保持同一个主题，只是把分析粒度从汇总改成了按天展开。",
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
                    "reason": "主题保持不变，用户只是调整了排序偏好和返回条数。",
                    "context_delta": {
                        "replace_sort": [{"field": "sales_qty", "order": "desc"}],
                        "replace_limit": 10,
                    },
                },
            },
        ]
