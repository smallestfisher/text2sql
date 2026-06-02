from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.models.retrieval import RetrievalContext
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.prompts.sql_prompt_context_assembler import SqlPromptContextAssembler

if TYPE_CHECKING:
    from backend.app.services.prompt_builder import PromptBuilder


class SqlGenerationPromptBuilder:
    def __init__(
        self,
        prompt_builder: PromptBuilder,
        context_assembler: SqlPromptContextAssembler | None = None,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._context_assembler = context_assembler or SqlPromptContextAssembler(prompt_builder)

    def build(
        self,
        context: SqlGenerationContext,
        retrieval: RetrievalContext | None = None,
        question: str | None = None,
    ) -> dict:
        builder = self._prompt_builder
        prompt_context = self._context_assembler.assemble(context, retrieval)
        return {
            "task": "oracle_text2sql",
            "question": question,
            "semantic_brief": context.semantic_brief,
            "retrieval_context": {
                "business_knowledge": prompt_context.business_knowledge,
                "examples": prompt_context.retrieved_examples,
                "join_patterns": prompt_context.selected_join_patterns,
            },
            "oracle_sql_rules": {
                "name": builder.sql_dialect.name,
                "label": builder.sql_dialect.label,
                "result_limit_clause": builder.sql_dialect.result_limit_clause_name,
            },
            "available_tables": prompt_context.source_schemas,
            "context_budget": prompt_context.context_budget,
            "context_summary": prompt_context.context_summary,
            "instructions": {
                "return_format": "sql_only",
                "constraints": builder._sql_generation_constraints(),
                "sql_preferences": prompt_context.sql_preferences,
            },
            "evidence_context": prompt_context.evidence_context,
        }
