from __future__ import annotations

from backend.app.core.settings import settings
from backend.app.repositories.auth_repository import FileAuthRepository
from backend.app.repositories.metadata_repository import FileMetadataRepository
from backend.app.repositories.audit_repository import FileAuditRepository, InMemoryAuditRepository
from backend.app.repositories.feedback_repository import FileFeedbackRepository, InMemoryFeedbackRepository
from backend.app.repositories.session_repository import FileSessionRepository, InMemorySessionRepository
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.audit_service import AuditService
from backend.app.services.auth_service import AuthService
from backend.app.services.database_connector import DatabaseConnector
from backend.app.services.evaluation_service import EvaluationService
from backend.app.services.feedback_service import FeedbackService
from backend.app.services.llm_client import LLMClient
from backend.app.services.metadata_service import MetadataService
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.permission_service import PermissionService
from backend.app.services.policy_engine import PolicyEngine
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.query_plan_compiler import QueryPlanCompiler
from backend.app.services.query_plan_validator import QueryPlanValidator
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.semantic_loader import SemanticLayerLoader
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.session_service import SessionService
from backend.app.services.session_state_service import SessionStateService
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.sql_executor import SqlExecutor
from backend.app.services.sql_generator import SqlGenerator
from backend.app.services.sql_validator import SqlValidator


class AppContainer:
    def __init__(self) -> None:
        self.settings = settings
        self.semantic_loader = SemanticLayerLoader()
        self.semantic_layer = self.semantic_loader.load()
        self.semantic_runtime = SemanticRuntime(self.semantic_layer)
        self.auth_repository = FileAuthRepository()

        self.query_planner = QueryPlanner(
            semantic_layer=self.semantic_layer,
            semantic_runtime=self.semantic_runtime,
        )
        self.query_plan_validator = QueryPlanValidator(semantic_runtime=self.semantic_runtime)
        self.policy_engine = PolicyEngine(semantic_runtime=self.semantic_runtime)
        self.permission_service = PermissionService(policy_engine=self.policy_engine)
        self.query_plan_compiler = QueryPlanCompiler(
            semantic_runtime=self.semantic_runtime,
            default_limit=self.settings.default_sql_limit,
        )
        self.session_state_service = SessionStateService()
        self.database_connector = DatabaseConnector(
            database_url=self.settings.database_url,
            timeout_seconds=self.settings.sql_timeout_seconds,
        )
        self.sql_executor = SqlExecutor(database_connector=self.database_connector)
        self.sql_generator = SqlGenerator(semantic_runtime=self.semantic_runtime)
        self.sql_ast_validator = SqlAstValidator()
        self.sql_validator = SqlValidator(
            ast_validator=self.sql_ast_validator,
            semantic_runtime=self.semantic_runtime,
            max_limit=self.settings.default_sql_limit,
        )
        self.auth_service = AuthService(
            repository=self.auth_repository,
            token_secret=self.settings.auth_token_secret,
            token_ttl_seconds=self.settings.auth_token_ttl_seconds,
        )
        self.retrieval_service = RetrievalService(
            semantic_layer=self.semantic_layer,
            semantic_runtime=self.semantic_runtime,
        )
        self.prompt_builder = PromptBuilder(semantic_runtime=self.semantic_runtime)
        self.llm_client = LLMClient(
            model_name=self.settings.openai_model,
            api_key=self.settings.openai_api_key,
            api_base=self.settings.openai_api_base,
        )
        self.answer_builder = AnswerBuilder()

        if self.settings.runtime_storage_mode == "file":
            self.session_repository = FileSessionRepository()
            self.audit_repository = FileAuditRepository()
            self.feedback_repository = FileFeedbackRepository()
        else:
            self.session_repository = InMemorySessionRepository()
            self.audit_repository = InMemoryAuditRepository()
            self.feedback_repository = InMemoryFeedbackRepository()
        self.metadata_repository = FileMetadataRepository()
        self.session_service = SessionService(self.session_repository)
        self.audit_service = AuditService(self.audit_repository)
        self.feedback_service = FeedbackService(self.feedback_repository)
        self.metadata_service = MetadataService(
            metadata_repository=self.metadata_repository,
            semantic_loader=self.semantic_loader,
            audit_repository=self.audit_repository,
        )

        self.orchestrator = ConversationOrchestrator(
            query_planner=self.query_planner,
            query_plan_validator=self.query_plan_validator,
            permission_service=self.permission_service,
            query_plan_compiler=self.query_plan_compiler,
            session_state_service=self.session_state_service,
            sql_generator=self.sql_generator,
            sql_validator=self.sql_validator,
            sql_executor=self.sql_executor,
            prompt_builder=self.prompt_builder,
            llm_client=self.llm_client,
            answer_builder=self.answer_builder,
            retrieval_service=self.retrieval_service,
            session_service=self.session_service,
            audit_service=self.audit_service,
            semantic_layer=self.semantic_layer,
        )
        self.evaluation_service = EvaluationService(
            orchestrator=self.orchestrator,
        )
