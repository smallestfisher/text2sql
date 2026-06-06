from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING

from backend.app.models.retrieval import RetrievalContext
from backend.app.models.sql_generation_context import SqlGenerationContext

if TYPE_CHECKING:
    from backend.app.services.prompt_builder import PromptBuilder


@dataclass(frozen=True)
class SqlPromptContextBundle:
    selected_sources: list[str]
    selected_join_patterns: list[dict]
    source_schemas: dict
    time_resolution: dict
    retrieved_examples: list[dict]
    business_knowledge: str
    business_knowledge_source: str
    context_budget: dict
    context_summary: dict
    evidence_context: dict
    sql_preferences: list[str]


class SqlPromptContextAssembler:
    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._prompt_builder = prompt_builder

    def assemble(
        self,
        context: SqlGenerationContext,
        retrieval: RetrievalContext | None = None,
    ) -> SqlPromptContextBundle:
        builder = self._prompt_builder
        selected_sources = builder._selected_sources_for_sql(
            context,
            retrieval,
            include_join_pattern_hits=False,
        )
        selected_join_patterns = builder._selected_join_patterns(context, selected_sources, retrieval)
        selected_sources = builder._expand_sources_with_join_patterns(selected_sources, selected_join_patterns)
        selected_business_knowledge = builder._select_business_knowledge_entries(context, selected_sources, retrieval)
        selected_sources = builder._expand_sources_with_business_knowledge(selected_sources, selected_business_knowledge)
        prompt_context = context.model_copy(update={"tables": selected_sources})
        time_resolution = builder._time_resolution(prompt_context)
        retrieved_examples = builder._select_retrieved_examples(context, retrieval, selected_sources=selected_sources)
        source_schemas = {
            table_name: builder._compact_table_schema(
                table_name,
                builder._tables_metadata.get(table_name, {}),
            )
            for table_name in selected_sources
            if table_name in builder._tables_metadata
        }
        sql_preferences = builder._prompt_asset_strings("sql_generation", "base_preferences")
        if any(item.op == "latest_n" for item in context.filters) and not any("latest_n" in item for item in sql_preferences):
            sql_preferences = [
                *sql_preferences,
                "当过滤条件使用 latest_n 时，必须保留最新排序语义，并结合真实排序字段生成 SQL，例如使用 MAX(真实排序字段) 或等价排序表达式。",
            ]
        business_knowledge = builder._business_knowledge_for_context(context, selected_sources, retrieval)
        business_knowledge_source = builder._business_knowledge_source_for_context(context, selected_sources, retrieval)
        context_budget = {
            "business_knowledge_max_chars": builder.BUSINESS_KNOWLEDGE_MAX_CHARS,
            "business_knowledge_mode": "ranked_relevant_chunks",
            "table_schemas_mode": "retrieval_selected_tables_full_columns",
        }
        evidence_context = {
            "time_resolution": time_resolution,
            "allowed_sources": selected_sources,
            "limit": context.limit,
            "context_source": "retrieval_evidence",
        }
        table_schema_columns_count = {
            table_name: len(schema.get("columns", []))
            for table_name, schema in source_schemas.items()
            if isinstance(schema, dict)
        }
        prompt_diagnostics = {
            "available_table_count": len(source_schemas),
            "available_table_column_count": sum(table_schema_columns_count.values()),
            "retrieved_example_count": len(retrieved_examples),
            "retrieved_example_chars": len(json.dumps(retrieved_examples, ensure_ascii=False)),
            "join_pattern_count": len(selected_join_patterns),
            "evidence_context_key_count": len(evidence_context),
            "prompt_payload_chars": len(
                json.dumps(
                    {
                        "available_tables": source_schemas,
                        "retrieval_context": {
                            "business_knowledge": business_knowledge,
                            "examples": retrieved_examples,
                            "join_patterns": selected_join_patterns,
                        },
                        "context_budget": context_budget,
                        "evidence_context": evidence_context,
                    },
                    ensure_ascii=False,
                )
            ),
        }
        context_summary = {
            "selected_sources": selected_sources,
            "table_schemas_count": len(source_schemas),
            "table_schema_columns_count": table_schema_columns_count,
            "business_knowledge_chars": len(business_knowledge),
            "business_knowledge_source": business_knowledge_source,
            "time_resolution_count": len(time_resolution),
            "few_shot_used": bool(retrieved_examples),
            "retrieved_example_count": len(retrieved_examples),
            "retrieved_example_ids": [item["id"] for item in retrieved_examples],
            "subject_domain": context.subject_domain,
            "business_knowledge_entry_ids": builder._selected_business_knowledge_ids(context, selected_sources, retrieval),
            "join_pattern_ids": builder._selected_join_pattern_ids(selected_join_patterns),
            "prompt_diagnostics": prompt_diagnostics,
        }
        return SqlPromptContextBundle(
            selected_sources=selected_sources,
            selected_join_patterns=selected_join_patterns,
            source_schemas=source_schemas,
            time_resolution=time_resolution,
            retrieved_examples=retrieved_examples,
            business_knowledge=business_knowledge,
            business_knowledge_source=business_knowledge_source,
            context_budget=context_budget,
            context_summary=context_summary,
            evidence_context=evidence_context,
            sql_preferences=sql_preferences,
        )
