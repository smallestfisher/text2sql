from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import threading

from backend.app.core.settings import Settings
from backend.app.models.semantic_config import SemanticReleaseDetailRecord
from backend.app.repositories.db_semantic_config_repository import DbSemanticConfigRepository
from backend.app.repositories.vector_document_repository import VectorDocumentRepository
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.llm_client import LLMClient
from backend.app.services.metadata_registry import ASSET_NAMES, MetadataRegistry
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.question_analysis_service import QuestionAnalysisService
from backend.app.services.question_context_service import QuestionContextService
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_validator import SqlValidator
from backend.app.services.vector_corpus_store_service import VectorCorpusStoreService
from backend.app.services.vector_retriever import VectorRetriever


class SnapshotAssetSource:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = {
            name: deepcopy(snapshot[name])
            for name in ASSET_NAMES
            if name in snapshot
        }

    def read_all(self) -> dict[str, object]:
        return deepcopy(self.snapshot)


@dataclass(frozen=True)
class ReleaseRuntime:
    release: SemanticReleaseDetailRecord
    metadata_registry: MetadataRegistry
    domain_config: dict
    semantic_runtime: SemanticRuntime
    prompt_builder: PromptBuilder
    llm_client: LLMClient
    question_context_service: QuestionContextService
    question_analysis_service: QuestionAnalysisService
    sql_validator: SqlValidator
    retrieval_service: RetrievalService


class ReleaseRuntimeManager:
    def __init__(
        self,
        *,
        repository: DbSemanticConfigRepository,
        vector_document_repository: VectorDocumentRepository,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.vector_document_repository = vector_document_repository
        self.settings = settings
        self._cache: dict[str, ReleaseRuntime] = {}
        self._lock = threading.RLock()

    def active_release_id(self) -> str | None:
        return self.repository.get_active_release_id()

    def get(self, release_id: str | None = None) -> ReleaseRuntime:
        resolved_id = release_id or self.active_release_id()
        if not resolved_id:
            raise RuntimeError("no active semantic release; sync, configure, and publish first")
        with self._lock:
            cached = self._cache.get(resolved_id)
            if cached is not None:
                return cached
            release = self.repository.get_release(resolved_id)
            if release is None:
                raise RuntimeError(f"semantic release not found: {resolved_id}")
            if release.status not in {"active", "inactive"}:
                raise RuntimeError(
                    f"semantic release is not available for queries: {resolved_id} ({release.status})"
                )
            runtime = self._build(release)
            self._cache[resolved_id] = runtime
            return runtime

    def prepare_release(self, release_id: str, snapshot: dict) -> None:
        release = self.repository.get_release(release_id)
        if release is None:
            raise RuntimeError(f"semantic release not found: {release_id}")
        if release.snapshot != snapshot:
            raise RuntimeError("semantic release snapshot changed while preparing")
        runtime = self._build(release)
        with self._lock:
            self._cache[release_id] = runtime

    def invalidate(self, release_id: str | None = None) -> None:
        with self._lock:
            if release_id is None:
                self._cache.clear()
            else:
                self._cache.pop(release_id, None)

    def _build(self, release: SemanticReleaseDetailRecord) -> ReleaseRuntime:
        asset_source = SnapshotAssetSource(release.snapshot)
        metadata_registry = MetadataRegistry(asset_source=asset_source)
        domain_config_loader = DomainConfigLoader(
            tables_metadata_provider=lambda: metadata_registry.tables_metadata
        )
        domain_config = domain_config_loader.load()
        semantic_runtime = SemanticRuntime(
            domain_config,
            metadata_registry=metadata_registry,
        )
        prompt_builder = PromptBuilder(
            semantic_runtime=semantic_runtime,
            metadata_registry=metadata_registry,
        )
        llm_client = self._llm_client()
        question_context_service = QuestionContextService(
            llm_client=llm_client,
            prompt_builder=prompt_builder,
        )
        question_analysis_service = QuestionAnalysisService(
            domain_config=domain_config,
            semantic_runtime=semantic_runtime,
            llm_client=llm_client,
            prompt_builder=prompt_builder,
            question_context_service=question_context_service,
        )
        vector_retriever = self._vector_retriever()
        vector_store = VectorCorpusStoreService(
            repository=self.vector_document_repository,
            vector_retriever=vector_retriever,
            scope_id=release.id,
        )
        retrieval_service = RetrievalService(
            domain_config=domain_config,
            semantic_runtime=semantic_runtime,
            metadata_registry=metadata_registry,
            vector_retriever=vector_retriever,
            vector_corpus_store_service=vector_store,
            vector_top_k=self.settings.vector_top_k,
            # Restore the active release's persisted vector cache as soon as
            # its runtime is loaded. Existing documents are reused without a
            # remote embedding call; only changed or missing documents rebuild.
            prewarm_vector_index=(
                self.settings.enable_vector_retrieval
                and self.settings.prewarm_vector_retrieval
            ),
        )
        sql_validator = SqlValidator(
            ast_validator=SqlAstValidator(),
            semantic_runtime=semantic_runtime,
            max_limit=self.settings.default_sql_limit,
            high_risk_limit=self.settings.high_risk_sql_limit,
        )
        return ReleaseRuntime(
            release=release,
            metadata_registry=metadata_registry,
            domain_config=domain_config,
            semantic_runtime=semantic_runtime,
            prompt_builder=prompt_builder,
            llm_client=llm_client,
            question_context_service=question_context_service,
            question_analysis_service=question_analysis_service,
            sql_validator=sql_validator,
            retrieval_service=retrieval_service,
        )

    def _llm_client(self) -> LLMClient:
        return LLMClient(
            model_name=self.settings.llm_model,
            enable_thinking=self.settings.llm_enable_thinking,
            api_key=self.settings.openai_api_key,
            api_base=self.settings.openai_api_base,
            timeout_seconds=self.settings.llm_timeout_seconds,
            max_retries=self.settings.llm_max_retries,
            repair_max_retries=self.settings.sql_repair_max_retries,
            cache_ttl_seconds=self.settings.llm_cache_ttl_seconds,
            cache_max_entries=self.settings.llm_cache_max_entries,
            cache_prompt=self.settings.llm_cache_prompt,
        )

    def _vector_retriever(self) -> VectorRetriever:
        provider = (
            self.settings.vector_retrieval_provider
            if self.settings.enable_vector_retrieval
            else "disabled"
        )
        retriever = VectorRetriever(
            provider=provider,
            api_key=self.settings.vector_api_key,
            api_base=self.settings.vector_api_base,
            model_name=self.settings.vector_model,
            dimensions=self.settings.vector_dimensions,
            timeout_seconds=self.settings.vector_timeout_seconds,
        )
        if self.settings.enable_vector_retrieval and not retriever.enabled:
            return VectorRetriever(provider="disabled")
        return retriever
