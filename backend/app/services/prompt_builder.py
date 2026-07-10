from __future__ import annotations

from typing import Any

from backend.app.models.retrieval import RetrievalContext
from backend.app.models.session_state import SessionState
from backend.app.models.sql_generation_context import SqlGenerationContext
from backend.app.services.example_factory import ExampleFactory
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.prompts import (
    QuestionContextPromptBuilder,
    SqlGenerationPromptBuilder,
    SqlPromptContextAssembler,
)
from backend.app.services.prompts.prompt_evidence_toolkit import PromptEvidenceToolkit
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_dialect import SqlDialect


class PromptBuilder:
    """Facade for prompt construction. Evidence load/select/render lives on
    ``PromptEvidenceToolkit``; this class keeps the stable public API and
    composes question-context / SQL generation builders.
    """

    # Mirrors toolkit constants so existing ``builder.BUSINESS_KNOWLEDGE_*``
    # access sites keep working without behavior change.
    BUSINESS_KNOWLEDGE_MAX_CHARS = PromptEvidenceToolkit.BUSINESS_KNOWLEDGE_MAX_CHARS
    BUSINESS_KNOWLEDGE_MAX_ITEMS_PER_ENTRY = PromptEvidenceToolkit.BUSINESS_KNOWLEDGE_MAX_ITEMS_PER_ENTRY
    RETRIEVAL_BOOST_WEIGHT = PromptEvidenceToolkit.RETRIEVAL_BOOST_WEIGHT
    RETRIEVAL_FUSION_SCORE_CAP = PromptEvidenceToolkit.RETRIEVAL_FUSION_SCORE_CAP
    RETRIEVAL_PRESENCE_BONUS = PromptEvidenceToolkit.RETRIEVAL_PRESENCE_BONUS
    KNOWLEDGE_PRESENCE_BONUS = PromptEvidenceToolkit.KNOWLEDGE_PRESENCE_BONUS

    def __init__(
        self,
        semantic_runtime: SemanticRuntime | None = None,
        metadata_registry: MetadataRegistry | None = None,
    ) -> None:
        self.semantic_runtime = semantic_runtime
        self.metadata_registry = metadata_registry or MetadataRegistry()
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.sql_ast_validator = SqlAstValidator()
        example_factory = (
            ExampleFactory(semantic_runtime.domain_config, semantic_runtime)
            if semantic_runtime is not None
            else None
        )
        self._toolkit = PromptEvidenceToolkit(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=self.metadata_registry,
            sql_dialect=self.sql_dialect,
            sql_ast_validator=self.sql_ast_validator,
            example_factory=example_factory,
        )
        self.question_context_prompt_builder = QuestionContextPromptBuilder(self)
        self.sql_prompt_context_assembler = SqlPromptContextAssembler(self)
        self.sql_generation_prompt_builder = SqlGenerationPromptBuilder(
            self,
            context_assembler=self.sql_prompt_context_assembler,
        )

    def build_question_context_prompt(
        self,
        *,
        question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any] | None = None,
        include_history: bool = True,
    ) -> dict:
        return self.question_context_prompt_builder.build(
            question=question,
            session_state=session_state,
            parser_signals=parser_signals,
            include_history=include_history,
        )

    def conversation_summary(self, session_state: SessionState | None) -> str:
        return self.question_context_prompt_builder.conversation_summary(session_state)

    def build_sql_prompt(
        self,
        context: SqlGenerationContext,
        retrieval: RetrievalContext | None = None,
        question: str | None = None,
    ) -> dict:
        return self.sql_generation_prompt_builder.build(
            context,
            retrieval=retrieval,
            question=question,
        )


    @property
    def example_factory(self):
        return self._toolkit.example_factory

    @example_factory.setter
    def example_factory(self, value) -> None:
        # Tests may replace the factory after construction; keep toolkit in sync.
        self._toolkit.example_factory = value

    def __getattr__(self, name: str):
        # Assemblers and sibling builders historically called private helpers on
        # PromptBuilder. Forward those to the toolkit without re-declaring each.
        if name == "_toolkit":
            raise AttributeError(name)
        instance_dict = object.__getattribute__(self, "__dict__")
        toolkit = instance_dict.get("_toolkit")
        if toolkit is None:
            # Some unit tests construct via ``__new__`` without ``__init__``.
            toolkit = PromptEvidenceToolkit()
            instance_dict["_toolkit"] = toolkit
        if name.startswith("_"):
            try:
                return getattr(toolkit, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"{type(self).__name__!s} has no attribute {name!r}"
                ) from exc
        raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")
