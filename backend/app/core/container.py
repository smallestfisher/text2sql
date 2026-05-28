from __future__ import annotations

import logging

from backend.app.core.settings import settings
from backend.app.repositories.db_audit_repository import DbAuditRepository
from backend.app.repositories.db_evaluation_run_repository import DbEvaluationRunRepository
from backend.app.repositories.db_auth_repository import DbAuthRepository
from backend.app.repositories.db_feedback_repository import DbFeedbackRepository
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.repositories.db_session_repository import DbSessionRepository
from backend.app.repositories.db_vector_document_repository import DbVectorDocumentRepository
from backend.app.repositories.metadata_repository import FileMetadataRepository
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.auth_service import AuthService
from backend.app.services.chat_response_restore_service import ChatResponseRestoreService
from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.evaluation_service import EvaluationService
from backend.app.services.execution_cache_service import ExecutionCacheService
from backend.app.services.feedback_service import FeedbackService
from backend.app.services.intent_service import IntentService
from backend.app.services.intent_normalizer import IntentNormalizer
from backend.app.services.llm_client import LLMClient
from backend.app.services.metadata_service import MetadataService
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.progress_service import ProgressService
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_plan_compiler import QueryPlanCompiler
from backend.app.services.query_plan_validator import QueryPlanValidator
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.runtime_admin_service import RuntimeAdminService
from backend.app.services.domain_config_loader import DomainConfigLoader
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.session_service import SessionService
from backend.app.services.session_state_service import SessionStateService
from backend.app.services.session_workspace_service import SessionWorkspaceService
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_executor import SqlExecutor
from backend.app.services.sql_validator import SqlValidator
from backend.app.services.conversation_persistence_service import ConversationPersistenceService
from backend.app.services.vector_retriever import VectorRetriever
from backend.app.services.vector_corpus_store_service import VectorCorpusStoreService
from backend.app.services.runtime_store_initializer import RuntimeStoreInitializer


logger = logging.getLogger(__name__)

BUSINESS_SQL_DIALECT = "oracle"
RUNTIME_SQL_DIALECT = "mysql"


class AppContainer:
    def __init__(self) -> None:
        self.settings = settings
        logger.info(
            "container init start app_env=%s business_dialect=%s runtime_dialect=%s vector_enabled=%s",
            self.settings.app_env,
            BUSINESS_SQL_DIALECT,
            RUNTIME_SQL_DIALECT,
            self.settings.enable_vector_retrieval,
        )
        self.domain_config_loader = DomainConfigLoader()
        self.domain_config = self.domain_config_loader.load()
        self.metadata_registry = MetadataRegistry()
        self.semantic_runtime = SemanticRuntime(
            self.domain_config,
            metadata_registry=self.metadata_registry,
        )
        self.business_database_connector = DatabaseConnector(
            database_url=self.settings.business_database_url,
            timeout_seconds=self.settings.sql_timeout_seconds,
            max_result_rows=self.settings.execution_max_rows,
            slow_query_threshold_ms=self.settings.slow_query_threshold_ms,
            sql_dialect=BUSINESS_SQL_DIALECT,
        )
        self.runtime_database_connector = DatabaseConnector(
            database_url=self.settings.runtime_database_url,
            timeout_seconds=self.settings.sql_timeout_seconds,
            max_result_rows=self.settings.execution_max_rows,
            slow_query_threshold_ms=self.settings.slow_query_threshold_ms,
            sql_dialect=RUNTIME_SQL_DIALECT,
        )
        self._require_database_connection(
            self.business_database_connector,
            connector_name="business database",
            verify_readonly_session_settings=True,
        )
        self._require_database_connection(
            self.runtime_database_connector,
            connector_name="runtime database",
        )
        self.runtime_store_initializer = RuntimeStoreInitializer(self.runtime_database_connector)
        logger.debug("runtime schema init start")
        self.runtime_store_initializer.ensure_schema()
        logger.debug("runtime schema init done")
        self.auth_repository = DbAuthRepository(self.runtime_database_connector)
        self.session_repository = DbSessionRepository(self.runtime_database_connector)
        self.audit_repository = DbAuditRepository(self.runtime_database_connector)
        self.feedback_repository = DbFeedbackRepository(self.runtime_database_connector)
        self.runtime_log_repository = DbRuntimeLogRepository(self.runtime_database_connector)
        self.evaluation_run_repository = DbEvaluationRunRepository(self.runtime_database_connector)
        self.vector_document_repository = DbVectorDocumentRepository(self.runtime_database_connector)
        self.progress_service = ProgressService()
        self.conversation_persistence_service = ConversationPersistenceService(self.runtime_database_connector)

        self.prompt_builder = PromptBuilder(
            semantic_runtime=self.semantic_runtime,
            metadata_registry=self.metadata_registry,
        )
        self.llm_client = LLMClient(
            model_name=self.settings.llm_model,
            api_key=self.settings.openai_api_key,
            api_base=self.settings.openai_api_base,
            timeout_seconds=self.settings.llm_timeout_seconds,
            max_retries=self.settings.llm_max_retries,
            repair_max_retries=self.settings.sql_repair_max_retries,
            cache_ttl_seconds=self.settings.llm_cache_ttl_seconds,
            cache_max_entries=self.settings.llm_cache_max_entries,
        )
        self.intent_service = IntentService(
            llm_client=self.llm_client,
            prompt_builder=self.prompt_builder,
        )
        self.intent_normalizer = IntentNormalizer(self.semantic_runtime)
        self.query_planner = QueryPlanner(
            domain_config=self.domain_config,
            semantic_runtime=self.semantic_runtime,
            llm_client=self.llm_client,
            prompt_builder=self.prompt_builder,
            intent_service=self.intent_service,
            intent_normalizer=self.intent_normalizer,
            enable_chitchat_mode=self.settings.enable_chitchat_mode,
        )
        self.query_plan_validator = QueryPlanValidator(semantic_runtime=self.semantic_runtime)
        self.query_plan_compiler = QueryPlanCompiler(
            semantic_runtime=self.semantic_runtime,
            default_limit=self.settings.default_sql_limit,
        )
        self.session_state_service = SessionStateService()
        self.execution_cache_service = ExecutionCacheService(
            ttl_seconds=self.settings.execution_cache_ttl_seconds,
            max_entries=self.settings.execution_cache_max_entries,
        )
        self.sql_executor = SqlExecutor(
            database_connector=self.business_database_connector,
            execution_cache=self.execution_cache_service,
        )
        self.sql_ast_validator = SqlAstValidator()
        self.sql_validator = SqlValidator(
            ast_validator=self.sql_ast_validator,
            semantic_runtime=self.semantic_runtime,
            max_limit=self.settings.default_sql_limit,
            high_risk_limit=self.settings.high_risk_sql_limit,
        )
        self.auth_service = AuthService(
            repository=self.auth_repository,
            token_secret=self.settings.auth_token_secret,
            token_ttl_seconds=self.settings.auth_token_ttl_seconds,
        )
        vector_provider = (
            self.settings.vector_retrieval_provider
            if self.settings.enable_vector_retrieval
            else "disabled"
        )
        self.vector_retriever = VectorRetriever(
            provider=vector_provider,
            api_key=self.settings.vector_api_key,
            api_base=self.settings.vector_api_base,
            model_name=self.settings.vector_model,
            dimensions=self.settings.vector_dimensions,
            timeout_seconds=self.settings.vector_timeout_seconds,
        )
        if self.settings.enable_vector_retrieval and not self.vector_retriever.enabled:
            raise RuntimeError("ENABLE_VECTOR_RETRIEVAL is true but vector embedding client is not configured")
        self.vector_corpus_store_service = VectorCorpusStoreService(
            repository=self.vector_document_repository,
            vector_retriever=self.vector_retriever,
        )
        self.retrieval_service = RetrievalService(
            domain_config=self.domain_config,
            semantic_runtime=self.semantic_runtime,
            metadata_registry=self.metadata_registry,
            vector_retriever=self.vector_retriever,
            vector_corpus_store_service=self.vector_corpus_store_service,
            vector_top_k=self.settings.vector_top_k,
            async_vector_index=False,
            prewarm_vector_index=self.settings.enable_vector_retrieval and self.settings.prewarm_vector_retrieval,
        )
        logger.info(
            "container init done vector_provider=%s prewarm_vector=%s",
            vector_provider,
            self.settings.enable_vector_retrieval and self.settings.prewarm_vector_retrieval,
        )
        self.answer_builder = AnswerBuilder(
            enable_chitchat_mode=self.settings.enable_chitchat_mode,
        )
        self.metadata_repository = FileMetadataRepository(self.metadata_registry)
        self.session_service = SessionService(self.session_repository)
        self.audit_service = AuditService(self.audit_repository)
        self.chat_response_restore_service = ChatResponseRestoreService(
            audit_service=self.audit_service,
            runtime_log_repository=self.runtime_log_repository,
        )
        self.session_workspace_service = SessionWorkspaceService(
            session_service=self.session_service,
            runtime_log_repository=self.runtime_log_repository,
            audit_service=self.audit_service,
            response_restore_service=self.chat_response_restore_service,
        )
        self.feedback_service = FeedbackService(self.feedback_repository)
        self.metadata_service = MetadataService(
            metadata_repository=self.metadata_repository,
            domain_config_loader=self.domain_config_loader,
            audit_repository=self.audit_repository,
        )
        self.runtime_admin_service = RuntimeAdminService(
            session_repository=self.session_repository,
            runtime_log_repository=self.runtime_log_repository,
        )

        self.orchestrator = ConversationOrchestrator(
            query_planner=self.query_planner,
            query_plan_validator=self.query_plan_validator,
            query_plan_compiler=self.query_plan_compiler,
            session_state_service=self.session_state_service,
            sql_validator=self.sql_validator,
            sql_executor=self.sql_executor,
            prompt_builder=self.prompt_builder,
            llm_client=self.llm_client,
            answer_builder=self.answer_builder,
            retrieval_service=self.retrieval_service,
            session_service=self.session_service,
            audit_service=self.audit_service,
            progress_service=self.progress_service,
            runtime_log_repository=self.runtime_log_repository,
            conversation_persistence_service=self.conversation_persistence_service,
            domain_config=self.domain_config,
        )
        self.evaluation_service = EvaluationService(
            orchestrator=self.orchestrator,
            evaluation_run_repository=self.evaluation_run_repository,
            session_repository=self.session_repository,
            runtime_log_repository=self.runtime_log_repository,
            auth_service=self.auth_service,
            response_restore_service=self.chat_response_restore_service,
        )

    def _require_database_connection(
        self,
        database_connector: DatabaseConnector,
        *,
        connector_name: str,
        verify_readonly_session_settings: bool = False,
    ) -> None:
        health = database_connector.test_connection(
            verify_readonly_session_settings=verify_readonly_session_settings,
        )
        if health.get("connected"):
            logger.debug(
                "database connection ok name=%s dialect=%s verify_readonly=%s",
                connector_name,
                health.get("sql_dialect"),
                verify_readonly_session_settings,
            )
            return
        logger.error(
            "database connection failed name=%s dialect=%s error=%s",
            connector_name,
            health.get("sql_dialect"),
            health.get("error"),
        )
        raise RuntimeError(
            f"{connector_name} is not ready: {health.get('error') or 'database connector is not configured'}"
        )
