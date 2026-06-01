from __future__ import annotations

import re
from typing import Any

from backend.app.models.example_library import ExampleRecord
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.services.example_factory import ExampleFactory
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_dialect import SqlDialect


class PromptBuilder:
    BUSINESS_KNOWLEDGE_MAX_CHARS = 1600
    BUSINESS_KNOWLEDGE_MAX_ITEMS_PER_ENTRY = 3

    def __init__(
        self,
        semantic_runtime: SemanticRuntime | None = None,
        metadata_registry: MetadataRegistry | None = None,
    ) -> None:
        self.semantic_runtime = semantic_runtime
        self.metadata_registry = metadata_registry or MetadataRegistry()
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.sql_ast_validator = SqlAstValidator()
        self.example_factory = (
            ExampleFactory(semantic_runtime.domain_config, semantic_runtime)
            if semantic_runtime is not None
            else None
        )

    def build_question_context_prompt(
        self,
        *,
        question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any] | None = None,
    ) -> dict:
        parser_signals = parser_signals or {}
        subject_domain = str(parser_signals.get("subject_domain") or (session_state.subject_domain if session_state else "unknown"))
        focus_tables = self._question_context_focus_tables(subject_domain, parser_signals, session_state)
        conversation_summary = self._conversation_summary(session_state) if session_state is not None else ""
        recent_turns = [
            self._turn_text(item)
            for item in (session_state.recent_turns[-4:] if session_state is not None else [])
        ]
        business_knowledge_excerpt = self._question_context_business_knowledge(
            subject_domain=subject_domain,
            question=question,
            conversation_summary=conversation_summary,
            recent_turns=recent_turns,
        )[:1200]
        table_fields = self._context_table_fields(focus_tables)
        return {
            "task": "question_context_generation",
            "question": question,
            "conversation_summary": conversation_summary,
            "last_turn": self._last_turn_payload(session_state),
            "recent_turns": recent_turns,
            "context_hints": self._compact_mapping(
                {
                    "parser_observations": self._compact_mapping(parser_signals),
                    "pending_clarification": self._pending_clarification_payload(session_state),
                    "business_knowledge_excerpt": business_knowledge_excerpt,
                    "focus_tables": focus_tables,
                    "table_fields": table_fields,
                }
            ),
            "instructions": {
                "return_format": "json",
                "fields": [
                    "decision",
                    "context_relation",
                    "subject_domain",
                    "effective_question",
                    "semantic_brief",
                    "clarification_question",
                    "reason",
                ],
                "decision_values": ["answerable", "clarification_needed", "invalid"],
                "context_relation_values": ["new", "follow_up", "ambiguous"],
                "constraints": [
                    "只做问题上下文整理，不生成 SQL。",
                    "如果当前问题是追问，effective_question 必须改写成不依赖上下文也能理解的完整自然语言问题。",
                    "首问只要本身是一个完整的自然语言业务查询句，就必须返回 decision=answerable、context_relation=new，并把原问题作为 effective_question；不要在 question_context 阶段追问字段、表、SQL 实现、可选维度、可选过滤条件、额外时间范围或业务口径细节。",
                    "当 context_relation=new 时，effective_question 必须忠实保留当前用户原话的查询对象、指标、时间、版本、数量和条件；不得用历史上下文替换、覆盖或改写当前问题的明确信息。",
                    "完整业务查询句的判断只看用户是否表达了要查什么；即使后续 SQL 生成可能还需要选择字段、表、指标公式、默认口径或是否追加过滤条件，也应先进入下一步，不能在本阶段 clarification_needed。",
                    "如果用户是在纠正或澄清自己上一句话，并且纠正后的句子已经能独立表达查询目标，也必须返回 answerable；不要继续追问可选条件。",
                    "如果用户问“哪一个”“最多的是谁”“Top/排名”等，返回对象就是查询输出，不要把这个输出对象误当成必须由用户补充的过滤条件。",
                    "只有用户这句话缺少核心意图、是无法解析的省略追问且上下文也无法补全，或明显不是业务查询时，才返回 clarification_needed 或 invalid。",
                    "如果 context_hints.pending_clarification 存在，当前用户问题应优先视为对上一轮澄清问题的回答；必须结合 pending_clarification、conversation_summary 和用户回答生成完整 effective_question。",
                    "当用户对 pending_clarification 给出确认、否认或补充信息时，不要把“是的”“不是”“对”等确认词当成独立业务问题。",
                    "只能继承 conversation_summary 和 recent_turns 中明确出现的信息；不确定指代时返回 clarification_needed。",
                    "如果用户表达替换、删除或新增条件，必须在 effective_question 中自然语言表达出来。",
                    "短追问优先基于最近一轮用户问题补全；除非用户明确要求回到更早主题，不要跳回更早轮次的查询意图。",
                    "不要因为“分布”“情况”“统计”就自行补充用户没有明确提出的维度。",
                    "如果用户提到“最新”但没有给出具体时间，应在 semantic_brief 中保留最新口径，不要编造具体日期。",
                    "只判断用户这句话和可用会话上下文是否足以形成完整自然语言问题；不要判断业务知识、字段、表、计算方法或 SQL 是否足够。",
                    "semantic_brief 用自然语言说明用户真正要查什么，供后续检索和 SQL 生成使用。",
                    "只输出指定 JSON 字段，不要输出结构化业务规划字段。",
                    "不要输出 markdown。",
                ],
            },
        }

    def conversation_summary(self, session_state: SessionState | None) -> str:
        if session_state is None:
            return ""
        return self._conversation_summary(session_state)

    def _pending_clarification_payload(self, session_state: SessionState | None) -> dict | None:
        if session_state is None or session_state.pending_clarification is None:
            return None
        return session_state.pending_clarification.model_dump(mode="json", exclude_none=True)

    def _last_turn_payload(self, session_state: SessionState | None) -> dict | None:
        if session_state is None:
            return None
        latest = session_state.recent_turns[-1] if session_state.recent_turns else None
        return self._compact_mapping(
            {
                "question": getattr(latest, "question", None) if latest is not None else None,
                "effective_question": getattr(latest, "effective_question", None) if latest is not None else session_state.last_effective_question,
                "semantic_brief": getattr(latest, "semantic_brief", None) if latest is not None else session_state.last_semantic_brief,
            }
        ) or None

    def build_sql_prompt(
        self,
        context: SqlGenerationContext,
        retrieval: RetrievalContext | None = None,
        question: str | None = None,
    ) -> dict:
        selected_sources = self._selected_sources_for_sql(context, retrieval)
        selected_join_patterns = self._selected_join_patterns(context, selected_sources, retrieval)
        selected_sources = self._expand_sources_with_join_patterns(selected_sources, selected_join_patterns)
        prompt_context = context.model_copy(update={"tables": selected_sources})
        time_resolution = self._time_resolution(prompt_context)
        retrieved_examples = self._select_retrieved_examples(context, retrieval, selected_sources=selected_sources)
        source_schemas = {
            table_name: self._compact_table_schema(
                table_name,
                self._tables_metadata.get(table_name, {}),
            )
            for table_name in selected_sources
            if table_name in self._tables_metadata
        }
        sql_preferences = self._prompt_asset_strings("sql_generation", "base_preferences")
        if any(item.op == "latest_n" for item in context.filters) and not any("latest_n" in item for item in sql_preferences):
            sql_preferences = [
                *sql_preferences,
                "当过滤条件使用 latest_n 时，必须保留最新排序语义，并结合真实排序字段生成 SQL，例如使用 MAX(真实排序字段) 或等价排序表达式。",
            ]
        business_knowledge = self._business_knowledge_for_context(context, selected_sources, retrieval)
        business_knowledge_source = self._business_knowledge_source_for_context(context, selected_sources, retrieval)
        context_budget = {
            "business_knowledge_max_chars": self.BUSINESS_KNOWLEDGE_MAX_CHARS,
            "business_knowledge_mode": "ranked_relevant_chunks",
            "table_schemas_mode": "retrieval_selected_tables_full_columns",
        }
        context_summary = {
            "selected_sources": selected_sources,
            "table_schemas_count": len(source_schemas),
            "table_schema_columns_count": {
                table_name: len(schema.get("columns", []))
                for table_name, schema in source_schemas.items()
                if isinstance(schema, dict)
            },
            "business_knowledge_chars": len(business_knowledge),
            "business_knowledge_source": business_knowledge_source,
            "time_resolution_count": len(time_resolution),
            "few_shot_used": bool(retrieved_examples),
            "retrieved_example_count": len(retrieved_examples),
            "retrieved_example_ids": [item["id"] for item in retrieved_examples],
            "subject_domain": context.subject_domain,
            "business_knowledge_entry_ids": self._selected_business_knowledge_ids(context, selected_sources, retrieval),
            "join_pattern_ids": self._selected_join_pattern_ids(selected_join_patterns),
        }
        evidence_context = {
            "time_resolution": time_resolution,
            "allowed_sources": selected_sources,
            "limit": context.limit,
            "context_source": "retrieval_evidence",
        }
        return {
            "task": "oracle_text2sql",
            "question": question,
            "semantic_brief": context.semantic_brief,
            "retrieval_context": {
                "business_knowledge": business_knowledge,
                "examples": retrieved_examples,
                "join_patterns": selected_join_patterns,
            },
            "oracle_sql_rules": {
                "name": self.sql_dialect.name,
                "label": self.sql_dialect.label,
                "result_limit_clause": self.sql_dialect.result_limit_clause_name,
            },
            "available_tables": source_schemas,
            "context_budget": context_budget,
            "context_summary": context_summary,
            "instructions": {
                "return_format": "sql_only",
                "constraints": self._sql_generation_constraints(),
                "sql_preferences": sql_preferences,
            },
            "evidence_context": evidence_context,
        }

    def _selected_sources_for_sql(
        self,
        context: SqlGenerationContext,
        retrieval: RetrievalContext | None,
    ) -> list[str]:
        selected: list[str] = []
        for table_name in context.tables:
            if table_name in self._tables_metadata and table_name not in selected:
                selected.append(table_name)
        if retrieval is not None:
            for hit in retrieval.hits:
                for table_name in self._tables_from_retrieval_hit(hit):
                    if table_name in self._tables_metadata and table_name not in selected:
                        selected.append(table_name)
        if selected:
            return selected[:8]
        return list(self._tables_metadata.keys())[:8]

    def _expand_sources_with_join_patterns(
        self,
        selected_sources: list[str],
        selected_join_patterns: list[dict],
    ) -> list[str]:
        expanded = list(selected_sources)
        selected_set = set(selected_sources)
        if not selected_set:
            return expanded
        for pattern in selected_join_patterns:
            pattern_tables = pattern.get("tables", [])
            if not isinstance(pattern_tables, list):
                continue
            if selected_set and not selected_set.intersection(str(item) for item in pattern_tables if item):
                continue
            for table_name in pattern_tables:
                if not isinstance(table_name, str) or table_name not in self._tables_metadata:
                    continue
                if table_name not in expanded:
                    expanded.append(table_name)
        return expanded[:8]

    def _tables_from_retrieval_hit(self, hit: RetrievalHit) -> list[str]:
        tables: list[str] = []
        metadata_tables = hit.metadata.get("tables", [])
        if isinstance(metadata_tables, list):
            tables.extend(str(item) for item in metadata_tables if item)
        table = hit.metadata.get("table")
        if isinstance(table, str) and table:
            tables.append(table)
        return list(dict.fromkeys(tables))

    def _load_tables_metadata(self) -> dict:
        return self.metadata_registry.tables_metadata

    def _load_examples(self) -> dict[str, ExampleRecord]:
        payload = self.metadata_registry.examples_template
        examples: dict[str, ExampleRecord] = {}
        for index, item in enumerate(payload if isinstance(payload, list) else []):
            try:
                example = self.example_factory.normalize(item) if self.example_factory else ExampleRecord(**item)
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

    def _business_knowledge_for_context(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> str:
        return self._structured_business_knowledge_for_context(context, selected_sources, retrieval)

    def _business_knowledge_source_for_context(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> str:
        selected_entries = self._select_business_knowledge_entries(context, selected_sources, retrieval)
        if not selected_entries:
            return "none"
        if self._retrieved_knowledge_hit_scores(retrieval):
            return "structured_knowledge+retrieval"
        return "structured_knowledge"

    def _compact_table_schema(
        self,
        table_name: str,
        payload: dict,
    ) -> dict:
        return self._compact_mapping(
            {
                "description": payload.get("description"),
                "columns": self._compact_columns(payload.get("columns", [])),
                "MAIN_KEY": payload.get("MAIN_KEY"),
                "time_fields": payload.get("time_fields"),
                "relationships": payload.get("relationships"),
            }
        )

    def _compact_columns(self, raw_columns: list, relevant_columns: set[str] | None = None) -> list[str]:
        selected: list[str] = []
        for raw_column in raw_columns:
            column_text = str(raw_column).strip()
            if not column_text:
                continue
            column_name = column_text.split("(", 1)[0].strip()
            if relevant_columns is not None and column_name not in relevant_columns:
                continue
            selected.append(column_text)
        return selected

    def _relevant_table_columns(
        self,
        table_name: str,
        *,
        context: SqlGenerationContext,
        field_resolution: dict[str, dict[str, list[str]]],
        time_resolution: dict,
    ) -> set[str]:
        columns: set[str] = set()
        if self.semantic_runtime is None:
            return columns
        table_fields = set(self.semantic_runtime.table_fields(table_name))
        for field in self._flatten_values(field_resolution):
            column_name = self._column_name_from_field(field)
            if column_name in table_fields:
                columns.add(column_name)
        for resolution in time_resolution.values():
            if not isinstance(resolution, dict):
                continue
            for candidate in resolution.get("candidates", []):
                if not isinstance(candidate, dict):
                    continue
                column_name = self._column_name_from_field(candidate.get("field"))
                if column_name in table_fields:
                    columns.add(column_name)
        for item in [*context.dimensions, *(filter_item.field for filter_item in context.filters), *(sort_item.field for sort_item in context.sort)]:
            if item in table_fields:
                columns.add(item)
        for metric_name in context.metrics:
            columns.update(
                column
                for column in self.semantic_runtime.metric_expression_columns(metric_name, table_names=[table_name])
                if column in table_fields
            )
        table_metadata = self._tables_metadata.get(table_name, {})
        relationships = table_metadata.get("relationships", {}) if isinstance(table_metadata, dict) else {}
        if isinstance(relationships, dict):
            for source_field in relationships:
                if source_field in table_fields:
                    columns.add(source_field)
        return columns

    def _column_name_from_field(self, field: Any) -> str:
        value = str(field or "").strip()
        if not value:
            return ""
        return value.rsplit(".", 1)[-1]

    def _flatten_values(self, value: Any) -> list[Any]:
        if isinstance(value, dict):
            flattened: list[Any] = []
            for item in value.values():
                flattened.extend(self._flatten_values(item))
            return flattened
        if isinstance(value, list):
            flattened = []
            for item in value:
                flattened.extend(self._flatten_values(item))
            return flattened
        return [value]

    def _structured_business_knowledge_for_context(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> str:
        selected_entries = self._select_business_knowledge_entries(context, selected_sources, retrieval)
        if not selected_entries:
            return ""
        sections: list[str] = []
        total_chars = 0
        for entry in selected_entries:
            notes = self._ranked_business_knowledge_items(entry, context, selected_sources)
            if not notes:
                continue
            title = str(entry.get("id", "business_knowledge"))
            tables = ", ".join(entry.get("tables", [])) if isinstance(entry.get("tables"), list) else ""
            lines = [f"[{title}]"]
            if tables:
                lines.append(f"相关表: {tables}")
            for note in notes:
                lines.append(f"- {note}")
            block = "\n".join(lines)
            separator_chars = 2 if sections else 0
            projected = total_chars + separator_chars + len(block)
            if projected > self.BUSINESS_KNOWLEDGE_MAX_CHARS and sections:
                continue
            sections.append(block)
            total_chars = projected
            if total_chars >= self.BUSINESS_KNOWLEDGE_MAX_CHARS:
                break
        return "\n\n".join(sections)[: self.BUSINESS_KNOWLEDGE_MAX_CHARS]

    def _ranked_business_knowledge_items(
        self,
        entry: dict,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
    ) -> list[str]:
        notes = [
            str(note).strip()
            for note in entry.get("notes", [])
            if isinstance(note, str) and note.strip()
        ]
        if not notes:
            return []
        terms = self._business_knowledge_terms(context, selected_sources)
        scored = []
        for index, note in enumerate(notes):
            lower_note = note.lower()
            score = sum(1 for term in terms if term and term in lower_note)
            score += self._critical_business_knowledge_score(note)
            scored.append((score, -index, note))
        scored.sort(reverse=True)
        return [note for _score, _negative_index, note in scored[: self.BUSINESS_KNOWLEDGE_MAX_ITEMS_PER_ENTRY]]

    def _critical_business_knowledge_score(self, note: str) -> int:
        critical_terms = (
            "latest_n",
            "最新",
            "横向",
            "展开",
            "y/n",
            "is_",
            "time_resolution",
            "不要自行追加",
            "库龄",
            "ttl",
            "pm_version",
            "act_type",
            "达成率",
            "gap",
        )
        lower_note = note.lower()
        return sum(2 for term in critical_terms if term in lower_note)


    def _business_knowledge_terms(self, context: SqlGenerationContext, selected_sources: list[str] | None) -> set[str]:
        terms = {
            context.subject_domain,
            *(selected_sources or []),
            *context.tables,
            *context.metrics,
            *context.dimensions,
            *(item.field for item in context.filters),
            *self._semantic_terms(context.semantic_brief),
            *self._semantic_terms(context.reason or ""),
        }
        if context.version_context and context.version_context.field:
            terms.add(context.version_context.field)
        return {str(term).lower() for term in terms if term}

    def _semantic_terms(self, text: str | None) -> set[str]:
        if not text:
            return set()
        ascii_terms = {
            item.lower()
            for item in re.findall(r"[A-Za-z0-9_]+", text)
            if len(item) > 1
        }
        chinese_terms = {
            item
            for item in re.findall(r"[\u4e00-\u9fa5]{2,}", text)
            if len(item) >= 2
        }
        return ascii_terms.union(chinese_terms)

    def _select_business_knowledge_entries(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> list[dict]:
        if not self._business_knowledge:
            return []
        terms = self._business_knowledge_terms(context, selected_sources)
        knowledge_hit_scores = self._retrieved_knowledge_hit_scores(retrieval)
        selected: list[tuple[float, int, dict]] = []
        for index, entry in enumerate(self._business_knowledge):
            score = self._score_business_knowledge_entry(
                context,
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
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None = None,
    ) -> list[str]:
        return [
            str(entry.get("id"))
            for entry in self._select_business_knowledge_entries(context, selected_sources, retrieval)
            if entry.get("id")
        ]

    def _score_business_knowledge_entry(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        terms: set[str],
        entry: dict,
        knowledge_hit_scores: dict[str, float] | None = None,
    ) -> float:
        score = 0.0
        domains = {str(item).lower() for item in entry.get("domains", []) if item}
        if context.subject_domain and context.subject_domain.lower() in domains:
            score += 5
        entry_tables = {str(item).lower() for item in entry.get("tables", []) if item}
        for source in selected_sources or []:
            if source.lower() in entry_tables:
                score += 4
        for table_name in context.tables:
            if table_name.lower() in entry_tables:
                score += 4
        entry_keywords = {str(item).lower() for item in entry.get("keywords", []) if item}
        score += sum(1 for term in terms if term in entry_keywords)
        entry_text = " ".join(
            str(item)
            for item in [
                entry.get("id", ""),
                " ".join(str(value) for value in entry.get("domains", []) if value),
                " ".join(str(value) for value in entry.get("tables", []) if value),
                " ".join(str(value) for value in entry.get("keywords", []) if value),
                " ".join(str(value) for value in entry.get("notes", []) if value),
            ]
        ).lower()
        score += 0.5 * sum(1 for term in terms if term and term in entry_text)
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
        context: SqlGenerationContext,
        retrieval: RetrievalContext | None,
        *,
        selected_sources: list[str] | None = None,
    ) -> list[dict]:
        if retrieval is None:
            return []

        examples = self._load_examples()
        selected: list[tuple[float, int, dict]] = []
        for index, hit in enumerate(retrieval.hits):
            if hit.source_type != "example":
                continue
            example = examples.get(hit.source_id)
            if example is None:
                continue
            if not self._retrieved_example_matches_context(context, example, hit):
                continue
            payload = {
                "id": example.id,
                "question": example.question,
                "semantic_shape": self._example_semantic_shape(example),
                "tables": example.tables,
                "metrics": example.metrics,
                "dimensions": example.dimensions,
                "filters": [item.model_dump(mode="json") for item in example.filters],
                "result_shape": example.result_shape,
                "notes": example.notes,
                "matched_features": hit.matched_features[:5],
            }
            if self._example_sql_is_prompt_safe(example):
                payload["sql"] = example.sql
            else:
                payload["sql_omitted_reason"] = "example SQL failed Oracle read-only prompt safety checks"
            selected.append((self._score_retrieved_example(context, selected_sources, example, hit), -index, payload))
        selected.sort(reverse=True)
        return [payload for _score, _negative_index, payload in selected[:2]]

    def _score_retrieved_example(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        example: ExampleRecord,
        hit: RetrievalHit,
    ) -> float:
        score = hit.score
        if example.subject_domain == context.subject_domain:
            score += 5
        selected_tables = set(selected_sources or []).union(context.tables)
        table_overlap = selected_tables.intersection(example.tables)
        score += 4 * len(table_overlap)
        score += 2 * len(set(context.metrics).intersection(example.metrics))
        score += 1.5 * len(set(context.dimensions).intersection(example.dimensions))
        context_filter_fields = {item.field for item in context.filters}
        example_filter_fields = {item.field for item in example.filters}
        score += 1.5 * len(context_filter_fields.intersection(example_filter_fields))
        if any(feature.startswith(("metrics:", "filters:", "version:", "time_", "metric:")) for feature in hit.matched_features):
            score += 1
        return score

    def _example_sql_is_prompt_safe(self, example: ExampleRecord) -> bool:
        errors, _warnings = self.sql_ast_validator.validate(example.sql)
        if errors:
            return False
        inspection = self.sql_ast_validator.inspect(example.sql)
        if not inspection.has_select or inspection.statement_count != 1:
            return False
        lowered_sql = example.sql.lower()
        mysql_only_tokens = (" limit ", "date_format", "str_to_date", "date_add", "curdate", "`")
        return not any(token in f" {lowered_sql} " for token in mysql_only_tokens)

    def _example_semantic_shape(self, example: ExampleRecord) -> dict:
        payload = {
            "subject_domain": example.subject_domain,
            "question_type": example.question_type,
            "scenario": example.scenario,
            "coverage_tags": example.coverage_tags[:6],
            "join_path": example.join_path,
        }
        return self._compact_mapping(payload)

    def _selected_join_patterns(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        retrieval: RetrievalContext | None,
    ) -> list[dict]:
        candidates: dict[str, tuple[float, int, dict]] = {}
        if retrieval is not None:
            for index, hit in enumerate(retrieval.hits):
                if hit.source_type != "join_pattern":
                    continue
                payload = self._join_pattern_payload_from_hit(hit)
                score = hit.score + self._score_join_pattern_payload(context, selected_sources, payload)
                candidates[payload["id"]] = (score, -index, payload)

        for index, pattern in enumerate(self.metadata_registry.join_patterns):
            payload = self._join_pattern_payload_from_record(pattern)
            score = self._score_join_pattern_payload(context, selected_sources, payload)
            if score <= 0:
                continue
            existing = candidates.get(payload["id"])
            ranked = (score, -1000 - index, payload)
            if existing is None or ranked[0] > existing[0]:
                candidates[payload["id"]] = ranked

        ranked_patterns = sorted(candidates.values(), reverse=True)
        return [payload for _score, _negative_index, payload in ranked_patterns[:1]]

    def _join_pattern_payload_from_hit(self, hit: RetrievalHit) -> dict:
        return {
            "id": hit.source_id,
            "summary": hit.summary,
            "matched_features": hit.matched_features,
            "domains": hit.metadata.get("domains", []),
            "tables": hit.metadata.get("tables", []),
            "join_path": hit.metadata.get("join_path", []),
            "notes": hit.metadata.get("notes", []),
        }

    def _join_pattern_payload_from_record(self, pattern: dict) -> dict:
        pattern_id = str(pattern.get("id", "join_pattern"))
        notes = pattern.get("notes", [])
        return {
            "id": pattern_id,
            "summary": str(notes[0]) if isinstance(notes, list) and notes else pattern_id,
            "matched_features": ["metadata:table_overlap"],
            "domains": pattern.get("domains", []),
            "tables": pattern.get("tables", []),
            "join_path": pattern.get("join_path", []),
            "notes": notes,
        }

    def _score_join_pattern_payload(
        self,
        context: SqlGenerationContext,
        selected_sources: list[str] | None,
        payload: dict,
    ) -> float:
        pattern_tables = {str(item) for item in payload.get("tables", []) if item}
        source_overlap = pattern_tables.intersection(selected_sources or [])
        context_overlap = pattern_tables.intersection(context.tables)
        pattern_text = " ".join(
            str(item)
            for item in [
                payload.get("id", ""),
                payload.get("summary", ""),
                " ".join(str(value) for value in payload.get("join_path", []) if value),
                " ".join(str(value) for value in payload.get("notes", []) if value),
            ]
        ).lower()
        semantic_hits = sum(1 for term in self._semantic_terms(context.semantic_brief) if term and term in pattern_text)
        evidence_score = (4 * len(source_overlap)) + (3 * len(context_overlap)) + (0.5 * semantic_hits)
        if evidence_score <= 0:
            return 0.0
        domains = {str(item).lower() for item in payload.get("domains", []) if item}
        domain_bonus = 3 if context.subject_domain and context.subject_domain.lower() in domains else 0
        score = evidence_score + domain_bonus
        return score

    def _selected_join_pattern_ids(self, selected_join_patterns: list[dict]) -> list[str]:
        return [str(item["id"]) for item in selected_join_patterns if item.get("id")]

    def _retrieved_example_matches_context(
        self,
        context: SqlGenerationContext,
        example: ExampleRecord,
        hit: RetrievalHit,
    ) -> bool:
        if example.subject_domain == context.subject_domain:
            return True

        context_tables = set(context.tables)
        if context_tables and context_tables.intersection(example.tables):
            return True

        context_metrics = set(context.metrics)
        if context_metrics and context_metrics.intersection(example.metrics):
            return True

        context_filter_fields = {item.field for item in context.filters}
        example_filter_fields = {item.field for item in example.filters}
        if context_filter_fields and context_filter_fields.intersection(example_filter_fields):
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

    def _allowed_fields(self, context: SqlGenerationContext) -> set[str]:
        if self.semantic_runtime is None:
            return set()
        return self.semantic_runtime.allowed_fields_for_context(context)

    def _sql_allowed_fields(self, context: SqlGenerationContext) -> set[str]:
        if self.semantic_runtime is None:
            return self._allowed_fields(context)

        fields: set[str] = set()
        for table_name in context.tables:
            fields.update(self.semantic_runtime.table_fields(table_name))
        for metric_name in context.metrics:
            fields.update(
                self.semantic_runtime.metric_expression_columns(
                    metric_name,
                    table_names=context.tables,
                )
            )
        return fields or self._allowed_fields(context)

    def _field_resolution(
        self,
        context: SqlGenerationContext,
        time_resolution: dict | None = None,
    ) -> dict[str, dict]:
        return {
            "dimensions": self._field_resolution_map(context, context.dimensions),
            "filters": self._field_resolution_map(
                context,
                [item.field for item in context.filters],
                time_resolution=time_resolution,
            ),
            "metrics": {
                metric_name: self._physical_metric_candidates(context, metric_name)
                for metric_name in context.metrics
                if self._physical_metric_candidates(context, metric_name)
            },
            "sort": self._field_resolution_map(
                context,
                [item.field for item in context.sort],
            ),
        }

    def _field_resolution_map(
        self,
        context: SqlGenerationContext,
        fields: list[str],
        time_resolution: dict | None = None,
    ) -> dict[str, dict | list[str]]:
        resolved: dict[str, dict | list[str]] = {}
        time_resolution = time_resolution or {}
        for field in fields:
            physical_candidates = self._physical_candidates(context, field)
            if physical_candidates:
                time_filter_examples = self._time_filter_examples(field, time_resolution, context=context)
                if time_filter_examples:
                    resolved[field] = {
                        "physical_candidates": physical_candidates,
                        "filter_examples": time_filter_examples,
                    }
                else:
                    resolved[field] = physical_candidates
        return resolved

    def _time_filter_examples(
        self,
        logical_field: str,
        time_resolution: dict,
        *,
        context: SqlGenerationContext,
    ) -> list[str]:
        if logical_field not in {"biz_date", "biz_month", "demand_month"}:
            return []
        candidates = time_resolution.get(logical_field, {}).get("candidates", [])
        examples: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if not self._time_candidate_belongs_to_query_tables(candidate, context):
                continue
            for key in ("month_filter_example", "month_range_filter_example", "day_filter_example"):
                value = candidate.get(key)
                if isinstance(value, str) and value and value not in examples:
                    examples.append(value)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("month_filter_example", "month_range_filter_example", "day_filter_example"):
                value = candidate.get(key)
                if isinstance(value, str) and value and value not in examples:
                    examples.append(value)
        return examples[:6]

    def _time_candidate_belongs_to_query_tables(self, candidate: dict, context: SqlGenerationContext) -> bool:
        field = str(candidate.get("field") or "")
        if "." not in field:
            return False
        table_name = field.split(".", 1)[0]
        return table_name in set(context.tables)

    def _physical_candidates(self, context: SqlGenerationContext, logical_field: str) -> list[str]:
        if self.semantic_runtime is None:
            return []
        resolved = self.semantic_runtime.resolve_field_candidates(
            context.subject_domain,
            context.tables,
            logical_field,
        )
        physical_allowed = self._sql_allowed_fields(context)
        allowed_candidates = sorted(item for item in resolved if item in physical_allowed)
        qualified = self._qualify_columns(context, allowed_candidates)
        return qualified or allowed_candidates

    def _physical_metric_candidates(self, context: SqlGenerationContext, metric_name: str) -> list[str]:
        if self.semantic_runtime is None:
            return []
        metric_columns = sorted(
            self.semantic_runtime.metric_expression_columns(
                metric_name,
                table_names=context.tables,
            )
        )
        qualified = self._qualify_columns(context, metric_columns)
        return qualified or metric_columns

    def _output_shape(self, context: SqlGenerationContext, time_resolution: dict | None = None) -> dict:
        required_projection = list(context.dimensions)
        aggregate_metrics = list(context.metrics)
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

    def _time_resolution(self, context: SqlGenerationContext) -> dict:
        if self.semantic_runtime is None:
            return {}
        resolution: dict[str, dict] = {}
        for logical_field in ["biz_date", "biz_month"]:
            candidates = self._time_resolution_candidates(context, logical_field)
            if candidates:
                resolution[logical_field] = {
                    "candidates": candidates,
                }
        return resolution

    def _time_resolution_candidates(self, context: SqlGenerationContext, logical_field: str) -> list[dict]:
        if self.semantic_runtime is None:
            return []
        if logical_field == "biz_date":
            candidates: list[dict] = []
            for candidate in self.semantic_runtime.resolve_time_field_candidates(
                context.subject_domain,
                context.tables,
                logical_field,
            ):
                field_expr = candidate["qualified_field"]
                payload = {
                    "field": field_expr,
                    "grain": candidate.get("grain"),
                    "format": candidate.get("format"),
                }
                day_filter_example = self._day_filter_example(context, field_expr, candidate.get("format"))
                if day_filter_example:
                    payload["day_filter_example"] = day_filter_example
                month_range_example = self._month_range_filter_example(
                    context,
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
        sample_month = self._example_compact_month(context)
        for candidate in self.semantic_runtime.resolve_time_field_candidates(
            context.subject_domain,
            context.tables,
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
            if sample_month and not self._field_belongs_to_horizontal_source(field_expr):
                month_literal = self.semantic_runtime.format_time_literal(sample_month, candidate.get("format")) or sample_month
                payload["month_filter_example"] = f"{field_expr} = '{month_literal}'"
            candidates.append(payload)

        for candidate in self.semantic_runtime.resolve_time_field_candidates(
            context.subject_domain,
            context.tables,
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
                context,
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

    def _field_belongs_to_horizontal_source(self, field_expression: str) -> bool:
        if "." not in field_expression:
            return False
        table_name = field_expression.split(".", 1)[0]
        table_metadata = self._tables_metadata.get(table_name, {})
        if not isinstance(table_metadata, dict):
            return False
        description = str(table_metadata.get("description") or "").lower()
        if "横向表" in description or "横表" in description or "horizontal" in description:
            return True
        columns = table_metadata.get("columns", [])
        if not isinstance(columns, list):
            return False
        column_text = " ".join(str(item) for item in columns).lower()
        return "横向" in column_text or "horizontal" in column_text

    def _substring_function(self) -> str:
        return "SUBSTR"

    def _example_compact_month(self, context: SqlGenerationContext) -> str:
        if self.semantic_runtime is not None:
            for item in context.filters:
                if item.field not in {"biz_month", "demand_month"}:
                    continue
                candidate_values = [item.value]
                if item.op == "between" and isinstance(item.value, list):
                    candidate_values = list(item.value)
                for candidate_value in candidate_values:
                    compact_month = self.semantic_runtime.compact_month_value(str(candidate_value))
                    if compact_month:
                        return compact_month
            time_context = context.time_context
            if time_context and time_context.range:
                for candidate_value in [time_context.range.start, time_context.range.end]:
                    compact_month = self.semantic_runtime.compact_month_value(candidate_value)
                    if compact_month:
                        return compact_month
        return "202604"

    def _example_iso_day(self, context: SqlGenerationContext) -> str:
        if self.semantic_runtime is not None:
            for item in context.filters:
                if item.field != "biz_date":
                    continue
                candidate_values = [item.value]
                if item.op == "between" and isinstance(item.value, list):
                    candidate_values = list(reversed(item.value))
                for candidate_value in candidate_values:
                    iso_day = self.semantic_runtime.format_time_literal(str(candidate_value), "YYYY-MM-DD")
                    if iso_day:
                        return iso_day
            time_context = context.time_context
            if time_context and time_context.range:
                for candidate_value in [time_context.range.end, time_context.range.start]:
                    iso_day = self.semantic_runtime.format_time_literal(candidate_value, "YYYY-MM-DD")
                    if iso_day:
                        return iso_day
            sample_month = self._example_compact_month(context)
            month_range = self.semantic_runtime.month_range_literals(sample_month, "YYYY-MM-DD")
            if month_range:
                return month_range[1]
        return "2026-04-30"

    def _day_filter_example(
        self,
        context: SqlGenerationContext,
        field_expression: str,
        field_format: str | None,
    ) -> str | None:
        if self.semantic_runtime is None:
            return None
        iso_day = self._example_iso_day(context)
        literal = self.semantic_runtime.format_time_literal(iso_day, field_format)
        if not literal:
            return None
        return f"{field_expression} = '{literal}'"

    def _month_range_filter_example(
        self,
        context: SqlGenerationContext,
        field_expression: str,
        field_format: str | None,
    ) -> str | None:
        if self.semantic_runtime is None:
            return None
        compact_month = self._example_compact_month(context)
        literals = self.semantic_runtime.month_range_literals(compact_month, field_format)
        if not literals:
            return None
        start_literal, end_literal = literals
        return f"{field_expression} BETWEEN '{start_literal}' AND '{end_literal}'"

    def _has_latest_n_filter(self, context: SqlGenerationContext) -> bool:
        return any(item.op == "latest_n" for item in context.filters)

    def _qualify_columns(self, context: SqlGenerationContext, columns: list[str]) -> list[str]:
        if self.semantic_runtime is None:
            return []
        qualified: list[str] = []
        for column in columns:
            for table_name in context.tables:
                if column in self.semantic_runtime.table_fields(table_name):
                    candidate = f"{table_name}.{column}"
                    if candidate not in qualified:
                        qualified.append(candidate)
        return qualified

    def _domain_tables(self, subject_domain: str) -> list[str] | None:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return None
        return self.semantic_runtime.domain_tables(subject_domain)

    def _domain_business_knowledge(self, subject_domain: str) -> str:
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
            if projected > self.BUSINESS_KNOWLEDGE_MAX_CHARS and sections:
                continue
            sections.append(block)
            total_chars = projected
            if total_chars >= self.BUSINESS_KNOWLEDGE_MAX_CHARS:
                break
        return "\n\n".join(sections)[: self.BUSINESS_KNOWLEDGE_MAX_CHARS]

    def _question_context_business_knowledge(
        self,
        *,
        subject_domain: str,
        question: str,
        conversation_summary: str,
        recent_turns: list[str],
    ) -> str:
        if not self._business_knowledge:
            return ""
        query_text = "\n".join([question, conversation_summary, *recent_turns]).lower()
        selected: list[tuple[int, int, dict]] = []
        for index, entry in enumerate(self._business_knowledge):
            score = 0
            domains = {str(item).lower() for item in entry.get("domains", []) if item}
            if subject_domain != "unknown" and subject_domain.lower() in domains:
                score += 3
            for keyword in entry.get("keywords", []) if isinstance(entry.get("keywords"), list) else []:
                keyword_text = str(keyword).strip().lower()
                if keyword_text and keyword_text in query_text:
                    score += 2
            for table in entry.get("tables", []) if isinstance(entry.get("tables"), list) else []:
                table_text = str(table).strip().lower()
                if table_text and table_text in query_text:
                    score += 1
            if score > 0:
                selected.append((score, -index, entry))
        selected.sort(reverse=True)
        sections: list[str] = []
        total_chars = 0
        for _score, _negative_index, entry in selected:
            notes = entry.get("notes", [])
            if not isinstance(notes, list):
                continue
            block = "\n".join(
                f"- {note}"
                for note in notes[: self.BUSINESS_KNOWLEDGE_MAX_ITEMS_PER_ENTRY]
                if isinstance(note, str) and note.strip()
            )
            if not block:
                continue
            projected = total_chars + (2 if sections else 0) + len(block)
            if projected > self.BUSINESS_KNOWLEDGE_MAX_CHARS and sections:
                continue
            sections.append(block)
            total_chars = projected
            if total_chars >= self.BUSINESS_KNOWLEDGE_MAX_CHARS:
                break
        return "\n\n".join(sections)[: self.BUSINESS_KNOWLEDGE_MAX_CHARS]

    def _supported_domains(self) -> list[str]:
        domains: set[str] = set()
        for entry in self._business_knowledge:
            for domain_name in entry.get("domains", []) if isinstance(entry, dict) else []:
                if isinstance(domain_name, str) and domain_name and domain_name != "unknown":
                    domains.add(domain_name)
        for example in self._load_examples().values():
            domain_name = str(example.get("subject_domain") or "")
            if domain_name and domain_name != "unknown":
                domains.add(domain_name)
        return sorted(domains)

    def _semantic_fields(self, subject_domain: str) -> list[dict]:
        if self.semantic_runtime is None or subject_domain == "unknown":
            return []
        return self.semantic_runtime.semantic_field_metadata(subject_domain=subject_domain)[:20]

    def _conversation_summary(self, session_state: SessionState) -> str:
        if session_state.conversation_summary:
            return session_state.conversation_summary
        turns = []
        for turn in session_state.recent_turns[-4:]:
            if turn.semantic_brief:
                prefix = f"问题：{turn.question}。" if turn.question else ""
                turns.append(prefix + turn.semantic_brief)
            elif turn.summary:
                turns.append(turn.summary)
        if turns:
            return "\n".join(f"- {item}" for item in turns)
        if session_state.last_semantic_brief:
            return session_state.last_semantic_brief
        return ""

    def _prior_conversation_summary(self, session_state: SessionState) -> str:
        turns = session_state.recent_turns[:-1]
        summaries: list[str] = []
        for turn in turns[-3:]:
            if turn.semantic_brief:
                prefix = f"问题：{turn.question}。" if turn.question else ""
                summaries.append(prefix + turn.semantic_brief)
            elif turn.summary:
                summaries.append(turn.summary)
        return "\n".join(f"- {item}" for item in summaries)

    def _turn_text(self, turn) -> str:
        parts: list[str] = []
        question = getattr(turn, "question", None)
        effective_question = getattr(turn, "effective_question", None)
        semantic_brief = getattr(turn, "semantic_brief", None)
        summary = getattr(turn, "summary", None)
        if question:
            parts.append(f"用户：{question}")
        if effective_question and effective_question != question:
            parts.append(f"改写后：{effective_question}")
        if semantic_brief:
            parts.append(f"摘要：{semantic_brief}")
        elif summary:
            parts.append(f"摘要：{summary}")
        return "；".join(parts)

    def _question_context_focus_tables(
        self,
        subject_domain: str,
        parser_signals: dict[str, Any],
        session_state: SessionState | None,
    ) -> list[str]:
        tables: list[str] = []
        for table_name in (session_state.tables if session_state is not None else []):
            if table_name and table_name not in tables:
                tables.append(table_name)
        if self.semantic_runtime is not None:
            for metric_name in parser_signals.get("matched_metrics", []) or []:
                for table_name in self.semantic_runtime.metric_tables(str(metric_name)):
                    if table_name and table_name not in tables:
                        tables.append(table_name)
            for table_name in self.semantic_runtime.domain_tables(subject_domain) or []:
                if table_name and table_name not in tables:
                    tables.append(table_name)
        return tables[:8]

    def _context_table_fields(self, table_names: list[str]) -> dict[str, list[str]]:
        fields: dict[str, list[str]] = {}
        for table_name in table_names:
            metadata = self._tables_metadata.get(table_name, {})
            columns = metadata.get("columns", []) if isinstance(metadata, dict) else []
            selected = [str(item) for item in columns if isinstance(item, str) and item.strip()]
            if selected:
                fields[table_name] = selected[:40]
        return fields

    def _compact_mapping(self, payload: dict) -> dict:
        compacted = {}
        for key, value in payload.items():
            if value in (None, "", [], {}):
                continue
            compacted[key] = value
        return compacted

    def _prompt_assets(self) -> dict:
        return {}

    def _prompt_asset_strings(self, section: str, key: str) -> list[str]:
        values = self._prompt_asset_value(section, key)
        return [str(item) for item in values] if isinstance(values, list) else []

    def _prompt_asset_value(self, section: str, key: str):
        section_payload = self._prompt_assets().get(section, {})
        if not isinstance(section_payload, dict):
            return None
        return section_payload.get(key)

    def _sql_generation_constraints(self) -> list[str]:
        constraints = []
        configured_constraints = self._prompt_asset_strings("sql_generation", "base_constraints")
        if not configured_constraints:
            configured_constraints = [
                "只生成一条只读 SELECT 或 WITH ... SELECT 语句。",
                "只能使用 available_tables 中提供的真实表和字段。",
                "必须使用 Oracle 语法，并包含 FETCH FIRST n ROWS ONLY 结果限制。",
                "不要使用 SELECT *。",
                "不要使用 MySQL 专属语法，例如 LIMIT、DATE_FORMAT、STR_TO_DATE、DATE_ADD、CURDATE、反引号。",
                "只返回 SQL，不要返回 markdown、注释或解释。",
            ]
        for item in configured_constraints:
            if "MySQL" in item and "基于真实物理表" in item:
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
        configured_preferences = self._prompt_asset_strings("sql_generation", "latest_n_preferences")
        if not configured_preferences:
            configured_preferences = [
                "如果语义或过滤条件包含 latest_n，必须先定位真实排序字段，再限制到最新 N 个值。",
                "当 latest_n.count = 1 时，优先使用 MAX(真实排序字段) 形成单值过滤；当 latest_n.count > 1 时，可使用子查询 ORDER BY 真实排序字段 DESC FETCH FIRST N ROWS ONLY。",
            ]
        for item in configured_preferences:
            if "ORDER BY 真实排序字段 DESC LIMIT N" in item:
                preferences.append(
                    "当 latest_n.count = 1 时，优先使用 MAX(真实排序字段) 形成单值过滤；当 latest_n.count > 1 时，可使用子查询 ORDER BY 真实排序字段 DESC FETCH FIRST N ROWS ONLY。"
                )
                continue
            preferences.append(item)
        return preferences
