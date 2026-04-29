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
                "fields": [
                    "subject_domain",
                    "metrics",
                    "entities",
                    "dimensions",
                    "filters",
                    "time_context",
                    "version_context",
                    "analysis_mode",
                    "question_type",
                    "inherit_context",
                    "confidence",
                    "reason",
                ],
                "constraints": [
                    "优先尊重 shallow_parse 中已确定的高置信时间、版本、topN 和筛选信号。",
                    "只能使用系统已支持的业务域，不要发明新的 subject_domain。",
                    "如果 question 中的字段说法命中 domain_hints.semantic_fields.aliases，应优先返回对应 canonical field，不要自造近义字段名。",
                    "不要输出 SQL，不要输出 schema 解释，只返回结构化 intent。",
                    "如果无法确定字段或指标，可以留空，不要虚构。",
                ],
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
        sql_preferences = [
            "以 query_plan.tables 为真实数据库对象的首要依据。",
            "严格遵循 query_plan 里的 dimensions、filters、sort 和 limit，除非那样会生成无效 SQL。",
        ]
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
        if self._is_generic_oms_inventory_plan(query_plan, selected_sources, question):
            sql_preferences = [
                "当问题明确查询 OMS 库存，且指标是泛化的 inventory_qty / 库存，而用户没有显式指定 panel、glass 或具体库龄段时，默认同时返回两套库存口径：SUM(glass_qty) AS inventory_glass_qty 和 SUM(panel_qty) AS inventory_panel_qty。",
                "不要把 OMS 常规库存默认收窄成单一 panel_qty；只有用户明确要求 panel 口径时，才可以只返回 panel_qty；只有用户明确要求 glass 口径时，才可以只返回 glass_qty。",
                "只有当用户明确提到库龄、0~1月、1~2月、3~6月、6~12月、12~24月、24~36月、36月以上等库龄段时，才改用 ONE_AGE_panel_qty 到 EUGHT_AGE_panel_qty 这类库龄字段。",
                *sql_preferences,
            ]
        if "production_actuals" in selected_sources and any(
            item.field == "biz_month" for item in query_plan.filters
        ):
            sql_preferences = [
                "production_actuals 只有日字段 work_date，没有独立的月字段。若 query_plan.filters 包含 biz_month='YYYYMM'，必须把它展开成整月过滤，例如 work_date >= 'YYYY-MM-01' AND work_date < '下月1号'，或使用 DATE_FORMAT(work_date, '%Y%m')='YYYYMM'。",
                "不要把 biz_month='YYYYMM' 误写成 work_date='YYYY-MM-01' 这样的单日过滤。",
                *sql_preferences,
            ]
        if self._is_demand_plan(query_plan, selected_sources):
            sql_preferences = [
                "对于 p_demand/v_demand 这类横向需求表，MONTH 是起始需求月份。REQUIREMENT_QTY 对应 base MONTH，NEXT_REQUIREMENT 对应 base MONTH 加 1 个月，LAST_REQUIREMENT 对应 base MONTH 加 2 个月，MONTH4 到 MONTH7 对应 base MONTH 加 3 到 6 个月。",
                "如果 query_plan.filters 包含 demand_month='YYYYMM' 或 demand_month BETWEEN 两个月份，请先构造一个包含 PM_VERSION、FGCODE、客户维度、demand_month、demand_qty 的 CTE，并确保产出的每个 demand_month 都与筛选值保持相同的紧凑 YYYYMM 格式。",
                "如果外层 SQL 还要按 PM_VERSION 过滤，或者要和 latest_versions 做 IN/JOIN 比较，那么 demand_unpivot 的每个 UNION ALL 分支都必须显式产出 PM_VERSION，不能在 CTE 外层引用一个未投影出来的 PM_VERSION。",
                "当 MONTH 在真实表里以紧凑 YYYYMM 编码存储时，不要直接对原始 MONTH 调用 DATE_ADD、ADDDATE、DATE_FORMAT 或类似函数。应先把 MONTH 转成真实日期，再格式化回 YYYYMM；或者对于 REQUIREMENT_QTY 直接保留 base MONTH。",
                "如果目标 demand_month 就是 base MONTH，本月需求应直接把 REQUIREMENT_QTY 映射到 MONTH，不要做日期运算。",
                "如果 query_plan.filters 中 PM_VERSION 的 op 是 latest_n，应先用 SELECT PM_VERSION FROM <source table> GROUP BY PM_VERSION ORDER BY PM_VERSION DESC LIMIT count 计算最新 N 个版本。",
                *sql_preferences,
            ]
        elif self._is_plan_actual_input_compare(query_plan, selected_sources):
            uses_panel_metrics = self._uses_panel_input_compare(query_plan)
            approved_metric = "approved_input_panel_qty" if uses_panel_metrics else "approved_input_qty"
            actual_metric = "actual_input_panel_qty" if uses_panel_metrics else "actual_input_qty"
            gap_metric = "input_panel_gap_qty" if uses_panel_metrics else "input_gap_qty"
            rate_metric = "input_panel_achievement_rate" if uses_panel_metrics else "input_achievement_rate"
            approved_column = "target_in_panel_qty" if uses_panel_metrics else "target_IN_glass_qty"
            actual_column = "Panel_qty" if uses_panel_metrics else "GLS_qty"
            sql_preferences = [
                "审批版投入与实际投入对比时，审批侧使用 monthly_plan_approved，实际侧使用 production_actuals。",
                "如果 query_plan.filters 包含 act_type='投入'，实际侧必须保留 act_type='投入' 过滤。",
                "审批侧工厂字段使用 monthly_plan_approved.factory_code，并统一别名成 factory；实际侧工厂字段使用 production_actuals.FACTORY，并统一别名成 factory。",
                "月粒度对比时，不要直接把逻辑字段 biz_month 写成物理列。审批侧应使用 plan_month 或从 PLAN_date 映射月份；实际侧应使用 DATE_FORMAT(work_date, '%Y-%m') 或等价方式映射月份。",
                "如果 monthly_plan_approved.plan_month 的真实格式是 YYYY-MM，单月查询应优先过滤 plan_month='YYYY-MM'；多月窗口应比较 YYYY-MM 形式，不要用 YYYY-MM-DD 去比较 plan_month。",
                f"{approved_metric} 应基于 monthly_plan_approved 的 SUM({approved_column}) 聚合。",
                f"{actual_metric} 应基于 production_actuals 的 SUM({actual_column}) 聚合。",
                f"{gap_metric} 默认定义为 {actual_metric} - {approved_metric}。",
                f"{rate_metric} 默认定义为 {actual_metric} / {approved_metric}；如果 {approved_metric}=0，返回 NULL。",
                "优先先分别按月份、工厂聚合审批侧和实际侧，再做 JOIN 计算派生指标。",
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
                    "最终 SQL 的外层 SELECT 必须完整投影 query_plan.dimensions；不要只在 WHERE 中使用这些维度，或只在中间 CTE 里出现。",
                    "如果 query_plan.dimensions 包含逻辑时间维度，例如 biz_month，请把它映射成真实月字段或月表达式，并在外层 SELECT 中显式起别名，且该别名或同等表达式必须进入外层 GROUP BY。",
                    "只返回 SQL，不要返回 markdown 或解释。",
                ],
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

    def _is_demand_plan(self, query_plan: QueryPlan, selected_sources: list[str] | None) -> bool:
        sources = set(selected_sources or []) | set(query_plan.tables)
        return (
            query_plan.subject_domain == "demand"
            or bool({"p_demand", "v_demand"}.intersection(sources))
            or any(metric.startswith("demand") for metric in query_plan.metrics)
        )

    def _is_plan_actual_input_compare(self, query_plan: QueryPlan, selected_sources: list[str] | None) -> bool:
        sources = set(selected_sources or []) | set(query_plan.tables)
        metrics = set(query_plan.metrics)
        compare_metrics = {
            "approved_input_qty",
            "approved_input_panel_qty",
            "actual_input_qty",
            "actual_input_panel_qty",
            "input_gap_qty",
            "input_panel_gap_qty",
            "input_achievement_rate",
            "input_panel_achievement_rate",
        }
        return (
            query_plan.subject_domain == "plan_actual"
            and {"monthly_plan_approved", "production_actuals"}.issubset(sources)
            and len(metrics.intersection(compare_metrics)) >= 3
        )

    def _uses_panel_input_compare(self, query_plan: QueryPlan) -> bool:
        metrics = set(query_plan.metrics)
        return bool(
            {
                "approved_input_panel_qty",
                "actual_input_panel_qty",
                "input_panel_gap_qty",
                "input_panel_achievement_rate",
            }.intersection(metrics)
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

    def _is_generic_oms_inventory_plan(
        self,
        query_plan: QueryPlan,
        selected_sources: list[str],
        question: str | None,
    ) -> bool:
        if query_plan.subject_domain != "inventory":
            return False
        if "oms_inventory" not in selected_sources:
            return False
        if "inventory_qty" not in query_plan.metrics:
            return False
        normalized_question = (question or "").lower()
        explicit_single_volume_terms = (
            "glass",
            "gls",
            "panel",
        )
        if any(term in normalized_question for term in explicit_single_volume_terms):
            return False
        aging_terms = (
            "库龄",
            "0~1",
            "1~2",
            "2~3",
            "3~6",
            "6~12",
            "12~24",
            "24~36",
            "36月以上",
            "36个月以上",
        )
        return not any(term in (question or "") for term in aging_terms)

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
