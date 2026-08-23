from __future__ import annotations

import logging
from pathlib import Path

from backend.app.core.settings import settings
from backend.app.repositories.db_audit_repository import DbAuditRepository
from backend.app.repositories.db_evaluation_case_repository import DbEvaluationCaseRepository
from backend.app.repositories.db_evaluation_run_repository import DbEvaluationRunRepository
from backend.app.repositories.db_auth_repository import DbAuthRepository
from backend.app.repositories.db_feedback_repository import DbFeedbackRepository
from backend.app.repositories.db_runtime_log_repository import DbRuntimeLogRepository
from backend.app.repositories.db_session_repository import DbSessionRepository
from backend.app.repositories.file_vector_document_repository import FileVectorDocumentRepository
from backend.app.repositories.db_semantic_config_repository import DbSemanticConfigRepository
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.auth_service import AuthService
from backend.app.services.chat_response_restore_service import ChatResponseRestoreService
from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.evaluation_service import EvaluationService
from backend.app.services.execution_cache_service import ExecutionCacheService
from backend.app.services.feedback_service import FeedbackService
from backend.app.services.llm_client import LLMClient
from backend.app.services.oracle_schema_introspector import OracleSchemaIntrospector
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.progress_service import ProgressService
from backend.app.services.release_aware_orchestrator import ReleaseAwareOrchestrator
from backend.app.services.release_runtime_manager import ReleaseRuntime, ReleaseRuntimeManager
from backend.app.services.runtime_admin_service import RuntimeAdminService
from backend.app.services.semantic_config_service import SEMANTIC_ASSET_DEFAULTS, SemanticConfigService
from backend.app.services.session_service import SessionService
from backend.app.services.session_state_service import SessionStateService
from backend.app.services.session_workspace_service import SessionWorkspaceService
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_executor import SqlExecutor
from backend.app.services.conversation_persistence_service import ConversationPersistenceService
from backend.app.services.vector_retriever import VectorRetriever
from backend.app.services.runtime_store_initializer import RuntimeStoreInitializer


logger = logging.getLogger(__name__)

BUSINESS_SQL_DIALECT = "oracle"
RUNTIME_SQL_DIALECT = "sqlite"


class AppContainer:
    def __init__(self) -> None:
        # Track connectors so a rebuilt container can dispose stale pools.
        self._disposables: list[DatabaseConnector] = []

        # Deployment configuration comes only from environment variables and
        # secrets. The runtime store persists product state, never configuration.
        self.settings = settings
        self.runtime_database_connector = DatabaseConnector(
            database_url=self.settings.runtime_database_url,
            timeout_seconds=self.settings.sql_timeout_seconds,
            max_result_rows=self.settings.execution_max_rows,
            slow_query_threshold_ms=self.settings.slow_query_threshold_ms,
            sql_dialect=RUNTIME_SQL_DIALECT,
        )
        self._disposables.append(self.runtime_database_connector)
        self._require_database_connection(
            self.runtime_database_connector,
            connector_name="runtime database",
        )
        self.runtime_store_initializer = RuntimeStoreInitializer(self.runtime_database_connector)
        logger.debug("runtime schema init start")
        self.runtime_store_initializer.ensure_schema()
        logger.debug("runtime schema init done")

        self.oracle_schema_introspector = OracleSchemaIntrospector()
        logger.info(
            "container init start app_env=%s business_dialect=%s runtime_dialect=%s vector_enabled=%s",
            self.settings.app_env,
            BUSINESS_SQL_DIALECT,
            RUNTIME_SQL_DIALECT,
            self.settings.enable_vector_retrieval,
        )

        # The deployment owns exactly one Oracle business connection.
        # Connectivity remains observable from the admin database-status API so
        # the runtime UI is still reachable when Oracle is temporarily down.
        self.business_database_connector = DatabaseConnector(
            database_url=self.settings.business_database_url,
            timeout_seconds=self.settings.sql_timeout_seconds,
            max_result_rows=self.settings.execution_max_rows,
            slow_query_threshold_ms=self.settings.slow_query_threshold_ms,
            sql_dialect=BUSINESS_SQL_DIALECT,
        )
        self._disposables.append(self.business_database_connector)

        self.semantic_config_repository = DbSemanticConfigRepository(
            self.runtime_database_connector
        )
        # This registry is validation-only. Production query runtimes are built
        # from immutable release snapshots; these empty values never seed a
        # draft or participate in query execution.
        self.metadata_registry = MetadataRegistry(documents=SEMANTIC_ASSET_DEFAULTS)
        self.auth_repository = DbAuthRepository(self.runtime_database_connector)
        self.session_repository = DbSessionRepository(self.runtime_database_connector)
        self.audit_repository = DbAuditRepository(self.runtime_database_connector)
        self.feedback_repository = DbFeedbackRepository(self.runtime_database_connector)
        self.runtime_log_repository = DbRuntimeLogRepository(self.runtime_database_connector)
        self.evaluation_case_repository = DbEvaluationCaseRepository(
            self.runtime_database_connector
        )
        self.evaluation_run_repository = DbEvaluationRunRepository(self.runtime_database_connector)
        self.vector_document_repository = FileVectorDocumentRepository(
            Path(self.settings.vector_cache_dir)
        )
        self.release_runtime_manager = ReleaseRuntimeManager(
            repository=self.semantic_config_repository,
            vector_document_repository=self.vector_document_repository,
            settings=self.settings,
        )
        self.semantic_config_service = SemanticConfigService(
            repository=self.semantic_config_repository,
            business_connector=self.business_database_connector,
            schema_scope=self.settings.business_schema_scope(),
            metadata_registry=self.metadata_registry,
            introspector=self.oracle_schema_introspector,
            release_prepare=self.release_runtime_manager.prepare_release,
        )
        self.progress_service = ProgressService()
        self.conversation_persistence_service = ConversationPersistenceService(self.runtime_database_connector)

        self.llm_client = LLMClient(
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
        # Vector misconfiguration degrades instead of bricking the container:
        # if enabled but no client could be built, log and run as disabled so
        # the config UI stays reachable to fix the key. Retrieval that actually
        # needs vectors will surface the error at use time.
        if self.settings.enable_vector_retrieval and not self.vector_retriever.enabled:
            logger.warning(
                "vector retrieval enabled but embedding client not configured; running with vectors disabled"
            )
            self.vector_retriever = VectorRetriever(provider="disabled")
        logger.info(
            "container init done vector_provider=%s",
            vector_provider,
        )
        self.answer_builder = AnswerBuilder()
        self.session_service = SessionService(
            self.session_repository,
            active_release_id_provider=self.release_runtime_manager.active_release_id,
            require_active_release=True,
        )
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
        self.runtime_admin_service = RuntimeAdminService(
            session_repository=self.session_repository,
            runtime_log_repository=self.runtime_log_repository,
        )

        self.orchestrator = ReleaseAwareOrchestrator(
            runtime_manager=self.release_runtime_manager,
            session_service=self.session_service,
            orchestrator_factory=self._build_release_orchestrator,
            audit_service=self.audit_service,
        )
        self.evaluation_service = EvaluationService(
            orchestrator=self.orchestrator,
            evaluation_case_repository=self.evaluation_case_repository,
            evaluation_run_repository=self.evaluation_run_repository,
            session_repository=self.session_repository,
            runtime_log_repository=self.runtime_log_repository,
            auth_service=self.auth_service,
            response_restore_service=self.chat_response_restore_service,
        )

    def _build_release_orchestrator(
        self,
        runtime: ReleaseRuntime,
    ) -> ConversationOrchestrator:
        return ConversationOrchestrator(
            question_analysis_service=runtime.question_analysis_service,
            session_state_service=self.session_state_service,
            sql_validator=runtime.sql_validator,
            sql_executor=self.sql_executor,
            prompt_builder=runtime.prompt_builder,
            llm_client=runtime.llm_client,
            answer_builder=self.answer_builder,
            retrieval_service=runtime.retrieval_service,
            session_service=self.session_service,
            audit_service=self.audit_service,
            progress_service=self.progress_service,
            runtime_log_repository=self.runtime_log_repository,
            conversation_persistence_service=self.conversation_persistence_service,
            domain_config=runtime.domain_config,
            semantic_release_id=runtime.release.id,
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

    def dispose(self) -> None:
        """Release database connection pools held by this container. Called when
        a rebuilt container replaces this one so stale engines don't leak."""
        for connector in getattr(self, "_disposables", []):
            connector.dispose()
