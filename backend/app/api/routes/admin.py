from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.dependencies import get_container, get_current_user, require_admin_user
from backend.app.core.container import AppContainer
from backend.app.core.settings import FIELD_SPECS
from backend.app.models.admin import (
    AdminDashboardResponse,
    AdminMetricChangeRecord,
    AdminMetricsSummary,
    ConfigCollectionResponse,
    ConfigFieldRecord,
    MetadataOverview,
    RuntimeQueryLogCollectionResponse,
    RuntimeQueryLogRecord,
    RuntimeRetrievalLogRecord,
    RuntimeRetentionResponse,
    RuntimeRiskSummaryResponse,
    RuntimeSessionCollectionResponse,
    RuntimeSqlAuditRecord,
    SessionSnapshotRecord,
)
from backend.app.models.auth import (
    AdminPasswordResetRequest,
    RoleRecord,
    RoleUpsertRequest,
    UserCollectionResponse,
    UserContext,
    UserUpsertRequest,
)
from backend.app.models.conversation import SessionHistoryResponse
from backend.app.models.evaluation import (
    EvaluationCase,
    EvaluationCaseCollection,
    EvaluationReplayRequest,
    EvaluationReplayResult,
    EvaluationRunRecord,
    EvaluationRunRequest,
    EvaluationSummary,
    RuntimeQueryLogMaterializeCaseRequest,
)
from backend.app.models.feedback import FeedbackCollectionResponse, FeedbackSummary
from backend.app.models.semantic_config import (
    DatabaseStatusRecord,
    PhysicalCatalogRecord,
    SchemaSyncRequest,
    SchemaSyncResponse,
    SemanticAssetDraftRecord,
    SemanticAssetDraftUpdateRequest,
    SemanticDraftCollectionResponse,
    SemanticDraftUpdateResponse,
    SemanticPublishResponse,
    SemanticReleaseCollectionResponse,
    SemanticReleaseDetailResponse,
)
from backend.app.models.trace import TraceRecord


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_user)],
)

logger = logging.getLogger(__name__)
MAX_DASHBOARD_WORKERS = 6


@router.get("/database/status", response_model=DatabaseStatusRecord)
def get_database_status(
    container: AppContainer = Depends(get_container),
) -> DatabaseStatusRecord:
    return container.semantic_config_service.database_status()


@router.get("/database/catalog", response_model=PhysicalCatalogRecord)
def get_database_catalog(
    container: AppContainer = Depends(get_container),
) -> PhysicalCatalogRecord:
    catalog = container.semantic_config_service.get_catalog()
    if catalog is None:
        raise HTTPException(status_code=404, detail="physical catalog has not been synchronized")
    return catalog


@router.post("/database/sync", response_model=SchemaSyncResponse)
def sync_database_schema(
    request: SchemaSyncRequest,
    container: AppContainer = Depends(get_container),
) -> SchemaSyncResponse:
    try:
        catalog, draft, warnings = container.semantic_config_service.sync_catalog(
            include_views=request.include_views,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Oracle Schema 同步失败: {exc}") from exc
    return SchemaSyncResponse(
        database=container.semantic_config_service.database_status(),
        catalog=catalog,
        draft_version=draft.version,
        warnings=warnings,
    )


@router.get("/semantic/drafts", response_model=SemanticDraftCollectionResponse)
def list_semantic_drafts(
    container: AppContainer = Depends(get_container),
) -> SemanticDraftCollectionResponse:
    drafts = container.semantic_config_service.ensure_drafts()
    return SemanticDraftCollectionResponse(drafts=drafts, count=len(drafts))


@router.get("/semantic/drafts/{name}", response_model=SemanticAssetDraftRecord)
def get_semantic_draft(
    name: str,
    container: AppContainer = Depends(get_container),
) -> SemanticAssetDraftRecord:
    draft = container.semantic_config_repository.get_draft(name)
    if draft is None:
        raise HTTPException(status_code=404, detail="semantic draft not found")
    return draft


@router.put("/semantic/drafts/{name}", response_model=SemanticDraftUpdateResponse)
def update_semantic_draft(
    name: str,
    request: SemanticAssetDraftUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> SemanticDraftUpdateResponse:
    try:
        draft, warnings = container.semantic_config_service.save_draft(
            name=name,
            content=request.content,
            expected_version=request.expected_version,
        )
    except ValueError as exc:
        status_code = 409 if "version conflict" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return SemanticDraftUpdateResponse(draft=draft, warnings=warnings)


@router.get("/semantic/releases", response_model=SemanticReleaseCollectionResponse)
def list_semantic_releases(
    container: AppContainer = Depends(get_container),
) -> SemanticReleaseCollectionResponse:
    releases = container.semantic_config_repository.list_releases()
    return SemanticReleaseCollectionResponse(releases=releases, count=len(releases))


@router.post("/semantic/releases", response_model=SemanticPublishResponse)
def publish_semantic_release(
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> SemanticPublishResponse:
    try:
        release, warnings = container.semantic_config_service.publish(
            created_by=current_user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SemanticPublishResponse(release=release, warnings=warnings)


@router.get("/semantic/releases/{release_id}", response_model=SemanticReleaseDetailResponse)
def get_semantic_release(
    release_id: str,
    container: AppContainer = Depends(get_container),
) -> SemanticReleaseDetailResponse:
    release = container.semantic_config_repository.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="semantic release not found")
    return SemanticReleaseDetailResponse(release=release)


class EvaluationCaseUpsertRequest(BaseModel):
    case: dict


class RoleUpdateRequest(RoleUpsertRequest):
    pass


class RuntimeRetentionRequest(BaseModel):
    retention_days: int = 30


class RuntimeQueryLogMaterializeExampleRequest(BaseModel):
    example_id: str | None = None
    scenario: str | None = None
    coverage_tags: list[str] = Field(default_factory=list)
    notes: str | None = None


@router.get("/metrics/summary", response_model=AdminMetricsSummary)
def admin_metrics_summary(container: AppContainer = Depends(get_container)) -> AdminMetricsSummary:
    return _build_admin_metrics_summary(container)


@router.get("/dashboard", response_model=AdminDashboardResponse)
def admin_dashboard(
    user_limit: int = 20,
    user_offset: int = 0,
    log_limit: int = 20,
    log_offset: int = 0,
    container: AppContainer = Depends(get_container),
) -> AdminDashboardResponse:
    section_errors: dict[str, str] = {}
    sections: dict[str, tuple[Callable[[], object], object]] = {
        "runtime_status": (
            lambda: _build_runtime_status(container),
            _empty_runtime_status(),
        ),
        "metrics": (
            lambda: _build_admin_metrics_summary(container),
            _empty_admin_metrics_summary(),
        ),
        "metadata_overview": (
            lambda: _build_semantic_overview(container),
            MetadataOverview(
                semantic_version=None,
                semantic_domains=[],
                table_count=0,
                example_count=0,
                trace_count=0,
            ),
        ),
        "users": (
            lambda: container.auth_service.list_admin_users(limit=user_limit, offset=user_offset),
            UserCollectionResponse(users=[], count=0),
        ),
        "roles": (
            container.auth_service.list_roles,
            [],
        ),
        "query_logs": (
            lambda: container.runtime_admin_service.list_query_logs(limit=log_limit, offset=log_offset),
            RuntimeQueryLogCollectionResponse(query_logs=[], count=0),
        ),
        "feedback_summary": (
            lambda: container.feedback_service.summarize(limit=100),
            FeedbackSummary(),
        ),
        "evaluation_summary": (
            lambda: container.evaluation_service.summarize_runs(limit=50),
            EvaluationSummary(run_count=0, case_count=0, passed_count=0, failed_count=0),
        ),
        "runtime_sessions": (
            lambda: container.runtime_admin_service.list_sessions(limit=1, offset=0),
            RuntimeSessionCollectionResponse(sessions=[], count=0),
        ),
    }
    dashboard_data = _dashboard_sections(section_errors, sections)
    return AdminDashboardResponse(
        runtime_status=dashboard_data["runtime_status"],
        metrics=dashboard_data["metrics"],
        metadata_overview=dashboard_data["metadata_overview"],
        users=dashboard_data["users"],
        roles=dashboard_data["roles"],
        query_logs=dashboard_data["query_logs"],
        feedback_summary=dashboard_data["feedback_summary"],
        evaluation_summary=dashboard_data["evaluation_summary"],
        runtime_sessions=dashboard_data["runtime_sessions"],
        section_errors=section_errors,
    )


def _dashboard_sections(
    section_errors: dict[str, str],
    sections: dict[str, tuple[Callable[[], object], object]],
) -> dict[str, object]:
    results: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_DASHBOARD_WORKERS, len(sections))) as executor:
        futures = {
            executor.submit(builder): (section_name, fallback)
            for section_name, (builder, fallback) in sections.items()
        }
        for future in as_completed(futures):
            section_name, fallback = futures[future]
            try:
                results[section_name] = future.result()
            except Exception as exc:
                logger.warning(
                    "admin dashboard section failed section=%s error=%s",
                    section_name,
                    exc,
                    exc_info=True,
                )
                section_errors[section_name] = f"{type(exc).__name__}: {exc}"
                results[section_name] = fallback
    return results


def _build_admin_metrics_summary(container: AppContainer) -> AdminMetricsSummary:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)

    def build_metric(summary: dict[str, int]) -> AdminMetricChangeRecord:
        today = int(summary.get("today") or 0)
        yesterday = int(summary.get("yesterday") or 0)
        return AdminMetricChangeRecord(
            total=int(summary.get("total") or 0),
            today=today,
            yesterday=yesterday,
            delta=today - yesterday,
        )

    return AdminMetricsSummary(
        users=build_metric(
            _repository_count_summary(
                container.auth_repository,
                "count_users_created_summary",
                "count_users",
                "count_users_created_between",
                today_start,
                yesterday_start,
                tomorrow_start,
            )
        ),
        sessions=build_metric(
            _repository_count_summary(
                container.session_repository,
                "count_sessions_created_summary",
                "count_sessions",
                "count_sessions_created_between",
                today_start,
                yesterday_start,
                tomorrow_start,
            )
        ),
        query_logs=build_metric(
            _repository_count_summary(
                container.runtime_log_repository,
                "count_query_logs_created_summary",
                "count_query_logs",
                "count_query_logs_created_between",
                today_start,
                yesterday_start,
                tomorrow_start,
            )
        ),
        feedbacks=build_metric(
            _repository_count_summary(
                container.feedback_repository,
                "count_records_created_summary",
                "count_records",
                "count_records_created_between",
                today_start,
                yesterday_start,
                tomorrow_start,
            )
        ),
        generated_at=datetime.utcnow(),
    )


def _repository_count_summary(
    repository,
    summary_method_name: str,
    total_method_name: str,
    range_method_name: str,
    today_start: datetime,
    yesterday_start: datetime,
    tomorrow_start: datetime,
) -> dict[str, int]:
    summary_method = getattr(repository, summary_method_name, None)
    if callable(summary_method):
        return summary_method(today_start, yesterday_start, tomorrow_start)
    total_method = getattr(repository, total_method_name)
    range_method = getattr(repository, range_method_name)
    return {
        "total": total_method(),
        "today": range_method(today_start, tomorrow_start),
        "yesterday": range_method(yesterday_start, today_start),
    }


def _build_runtime_status(container: AppContainer) -> dict:
    probes: dict[str, tuple[Callable[[], dict], dict | None]] = {
        "business_database": (
            container.business_database_connector.test_connection,
            None,
        ),
        "runtime_database": (
            container.runtime_database_connector.test_connection,
            None,
        ),
        "llm": (container.llm_client.health, None),
        "vector_retrieval": (
            lambda: _active_vector_health(container),
            _empty_vector_retrieval_status(),
        ),
        "retrieval_corpus": (
            lambda: _active_retrieval_health(container),
            _empty_retrieval_corpus_status(),
        ),
        "sql_ast": (container.sql_ast_validator.health, None),
    }
    results = _runtime_probes(probes)
    return {probe_name: results[probe_name] for probe_name in probes}


def _active_retrieval_health(container: AppContainer) -> dict:
    release_id = container.release_runtime_manager.active_release_id()
    if not release_id:
        status = _empty_retrieval_corpus_status(
            "no active semantic release; sync, configure, and publish first"
        )
        status["status"] = "not_ready"
        return status
    runtime = container.release_runtime_manager.get(release_id)
    return runtime.retrieval_service.health()


def _active_vector_health(container: AppContainer) -> dict:
    """Report the vector index belonging to the active semantic release.

    ``container.vector_retriever`` is only a construction-time helper. Query
    traffic uses the release-scoped retriever owned by ``RetrievalService``;
    reporting the helper made a healthy persisted index appear as 0 documents.
    """
    release_id = container.release_runtime_manager.active_release_id()
    if not release_id:
        return container.vector_retriever.health()
    runtime = container.release_runtime_manager.get(release_id)
    return runtime.retrieval_service.vector_retriever.health()


def _build_semantic_overview(container: AppContainer) -> MetadataOverview:
    release_id = container.release_runtime_manager.active_release_id()
    release = (
        container.semantic_config_repository.get_release(release_id)
        if release_id
        else None
    )
    if release is None:
        return MetadataOverview(
            semantic_version=None,
            semantic_domains=[],
            table_count=0,
            example_count=0,
            trace_count=len(container.audit_repository.list_records()),
        )
    snapshot = release.snapshot
    domains: set[str] = set()
    examples = snapshot.get("examples_template", [])
    if isinstance(examples, list):
        domains.update(
            str(item.get("subject_domain"))
            for item in examples
            if isinstance(item, dict) and item.get("subject_domain")
        )
    knowledge = snapshot.get("business_knowledge", {})
    if isinstance(knowledge, dict):
        for item in knowledge.get("entries", []):
            if isinstance(item, dict):
                domains.update(str(value) for value in item.get("domains", []) if value)
    joins = snapshot.get("join_patterns", {})
    if isinstance(joins, dict):
        for item in joins.get("patterns", []):
            if isinstance(item, dict):
                domains.update(str(value) for value in item.get("domains", []) if value)
    tables = snapshot.get("tables_metadata", {})
    return MetadataOverview(
        semantic_version=f"v{release.version}",
        semantic_domains=sorted(domain for domain in domains if domain != "unknown"),
        table_count=len(tables) if isinstance(tables, dict) else 0,
        example_count=len(examples) if isinstance(examples, list) else 0,
        trace_count=len(container.audit_repository.list_records()),
    )


def _runtime_probes(probes: dict[str, tuple[Callable[[], dict], dict | None]]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(probes)) as executor:
        futures = {
            executor.submit(builder): (probe_name, fallback)
            for probe_name, (builder, fallback) in probes.items()
        }
        for future in as_completed(futures):
            probe_name, fallback = futures[future]
            try:
                results[probe_name] = future.result()
            except Exception as exc:
                results[probe_name] = _runtime_probe_error(probe_name, exc, fallback)
    return results


def _runtime_probe_error(probe_name: str, exc: Exception, fallback: dict | None) -> dict:
    logger.warning(
        "admin dashboard runtime probe failed probe=%s error=%s",
        probe_name,
        exc,
        exc_info=True,
    )
    if fallback is None:
        fallback = {"connected": False}
    return {
        **fallback,
        "status": "error",
        "error": str(exc),
    }


def _empty_admin_metrics_summary() -> AdminMetricsSummary:
    empty = AdminMetricChangeRecord(total=0, today=0, yesterday=0, delta=0)
    return AdminMetricsSummary(
        users=empty,
        sessions=empty,
        query_logs=empty,
        feedbacks=empty,
        generated_at=datetime.utcnow(),
    )


def _empty_runtime_status(error: str | None = None) -> dict:
    return {
        "business_database": _unavailable_health(error),
        "runtime_database": _unavailable_health(error),
        "llm": _unavailable_health(error),
        "vector_retrieval": _empty_vector_retrieval_status(error),
        "retrieval_corpus": _empty_retrieval_corpus_status(error),
        "sql_ast": _unavailable_health(error),
    }


def _unavailable_health(error: str | None = None) -> dict:
    status = {"connected": False, "status": "error"}
    if error:
        status["error"] = error
    return status


def _empty_vector_retrieval_status(error: str | None = None) -> dict:
    status = {
        "enabled": False,
        "provider": "unknown",
        "model": None,
        "api_base": None,
        "ready": False,
        "indexed_document_count": 0,
        "last_search_error": None,
        "loaded_embedding_signature": None,
        "configured_embedding_signature": None,
    }
    return status


def _empty_retrieval_corpus_status(error: str | None = None) -> dict:
    return {
        "vector_enabled": False,
        "vector_provider": "unknown",
        "vector_ready": False,
        "document_count": 0,
        "document_count_by_source": {},
        "example_count": 0,
        "join_pattern_count": 0,
        "vector_sync": {
            "persisted_document_count": 0,
            "reused_document_count": 0,
            "rebuilt_document_count": 0,
            "deleted_document_count": 0,
            "upserted_document_count": 0,
            "vector_sync_last_updated_at": None,
            "embedding_signature": None,
            "error": error,
            "pending_rebuild": False,
        },
    }


def _config_fields(container: AppContainer) -> list[ConfigFieldRecord]:
    """Render deployment settings without exposing secret values."""
    records: list[ConfigFieldRecord] = []
    for spec in FIELD_SPECS:
        env_raw = os.getenv(spec.env)
        source = "env" if env_raw is not None and env_raw.strip() != "" else "default"
        effective = getattr(container.settings, spec.attr, None)
        value = None if effective is None else str(effective)
        if spec.secret and value:
            value = "********"
        records.append(
            ConfigFieldRecord(
                name=spec.env,
                group=spec.group,
                type=spec.type,
                editable=False,
                secret=spec.secret,
                value=value,
                source=source,
            )
        )
    return records


@router.get("/config", response_model=ConfigCollectionResponse)
def get_config(
    container: AppContainer = Depends(get_container),
    _admin: UserContext = Depends(require_admin_user),
) -> ConfigCollectionResponse:
    return ConfigCollectionResponse(fields=_config_fields(container))


@router.get("/traces", response_model=list[TraceRecord])
def list_traces(container: AppContainer = Depends(get_container)) -> list[TraceRecord]:
    return container.audit_repository.list_records()


@router.get("/traces/{trace_id}", response_model=TraceRecord)
def get_trace(trace_id: str, container: AppContainer = Depends(get_container)) -> TraceRecord:
    trace = container.audit_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace


@router.get("/feedbacks", response_model=FeedbackCollectionResponse)
def list_feedbacks(
    session_id: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    container: AppContainer = Depends(get_container),
) -> FeedbackCollectionResponse:
    return container.feedback_service.list_records(
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        limit=limit,
    )


@router.get("/feedbacks/summary", response_model=FeedbackSummary)
def summarize_feedbacks(
    session_id: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    container: AppContainer = Depends(get_container),
) -> FeedbackSummary:
    return container.feedback_service.summarize(
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        limit=limit,
    )


@router.get("/runtime/status")
def runtime_status(container: AppContainer = Depends(get_container)) -> dict:
    return _build_runtime_status(container)


@router.post("/runtime/vector/prewarm")
def prewarm_runtime_vector_index(container: AppContainer = Depends(get_container)) -> dict:
    try:
        runtime = container.release_runtime_manager.get()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return runtime.retrieval_service.prewarm_vector_index()


@router.get("/runtime/sessions", response_model=RuntimeSessionCollectionResponse)
def list_runtime_sessions(
    limit: int = 50,
    offset: int = 0,
    container: AppContainer = Depends(get_container),
) -> RuntimeSessionCollectionResponse:
    return container.runtime_admin_service.list_sessions(limit=limit, offset=offset)


@router.get("/runtime/sessions/{session_id}/history", response_model=SessionHistoryResponse)
def get_runtime_session_history(
    session_id: str,
    container: AppContainer = Depends(get_container),
) -> SessionHistoryResponse:
    history = container.runtime_admin_service.get_session_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="session not found")
    return history


@router.get("/runtime/sessions/{session_id}/snapshots", response_model=list[SessionSnapshotRecord])
def list_runtime_session_snapshots(
    session_id: str,
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> list[SessionSnapshotRecord]:
    return container.runtime_admin_service.list_session_snapshots(session_id=session_id, limit=limit)


@router.get("/runtime/query-logs", response_model=RuntimeQueryLogCollectionResponse)
def list_runtime_query_logs(
    limit: int = 50,
    offset: int = 0,
    session_id: str | None = None,
    user_id: str | None = None,
    sql_risk_level: str | None = None,
    subject_domain: str | None = None,
    risk_flag: str | None = None,
    container: AppContainer = Depends(get_container),
) -> RuntimeQueryLogCollectionResponse:
    return container.runtime_admin_service.list_query_logs(
        limit=limit,
        offset=offset,
        session_id=session_id,
        user_id=user_id,
        sql_risk_level=sql_risk_level,
        subject_domain=subject_domain,
        risk_flag=risk_flag,
    )


@router.get("/runtime/query-logs/risk-summary", response_model=RuntimeRiskSummaryResponse)
def summarize_runtime_query_risks(
    limit: int = 200,
    container: AppContainer = Depends(get_container),
) -> RuntimeRiskSummaryResponse:
    return container.runtime_admin_service.summarize_query_risks(limit=limit)


@router.post("/runtime/retention/purge", response_model=RuntimeRetentionResponse)
def purge_runtime_retention(
    request: RuntimeRetentionRequest,
    container: AppContainer = Depends(get_container),
) -> RuntimeRetentionResponse:
    if request.retention_days < 1:
        raise HTTPException(status_code=400, detail="retention_days must be >= 1")
    return container.runtime_admin_service.purge_runtime_data(retention_days=request.retention_days)


@router.get("/runtime/query-logs/{trace_id}", response_model=RuntimeQueryLogRecord)
def get_runtime_query_log(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> RuntimeQueryLogRecord:
    record = container.runtime_admin_service.get_query_log(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="query log not found")
    return record


@router.get("/runtime/query-logs/{trace_id}/retrieval", response_model=list[RuntimeRetrievalLogRecord])
def list_runtime_retrieval_logs(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> list[RuntimeRetrievalLogRecord]:
    return container.runtime_admin_service.list_retrieval_logs(trace_id)


@router.get("/runtime/query-logs/{trace_id}/sql-audit", response_model=RuntimeSqlAuditRecord)
def get_runtime_sql_audit(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> RuntimeSqlAuditRecord:
    record = container.runtime_admin_service.get_sql_audit(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="sql audit not found")
    return record


@router.post("/runtime/query-logs/{trace_id}/replay", response_model=EvaluationReplayResult)
def replay_runtime_query_log(
    trace_id: str,
    request: EvaluationReplayRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationReplayResult:
    try:
        return container.evaluation_service.replay_trace(trace_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="query log not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runtime/query-logs/{trace_id}/materialize-case", response_model=EvaluationCase)
def materialize_runtime_query_log_as_case(
    trace_id: str,
    request: RuntimeQueryLogMaterializeCaseRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationCase:
    try:
        return container.evaluation_service.materialize_trace_as_case(trace_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="query log not found") from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post(
    "/runtime/query-logs/{trace_id}/materialize-example",
    response_model=SemanticDraftUpdateResponse,
)
def materialize_runtime_query_log_as_example(
    trace_id: str,
    request: RuntimeQueryLogMaterializeExampleRequest,
    container: AppContainer = Depends(get_container),
) -> SemanticDraftUpdateResponse:
    try:
        example = container.evaluation_service.materialize_trace_as_example(
            trace_id,
            example_id=request.example_id,
            scenario=request.scenario,
            coverage_tags=request.coverage_tags,
            notes=request.notes,
        )
        draft, warnings = container.semantic_config_service.add_example_to_draft(
            example,
        )
        return SemanticDraftUpdateResponse(draft=draft, warnings=warnings)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="query log not found") from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/users", response_model=UserCollectionResponse)
def list_users(
    limit: int = 50,
    offset: int = 0,
    container: AppContainer = Depends(get_container),
) -> UserCollectionResponse:
    return container.auth_service.list_admin_users(limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=UserContext)
def get_user(
    user_id: str,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    user = container.auth_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.put("/users/{user_id}", response_model=UserContext)
def upsert_user(
    user_id: str,
    request: UserUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> UserContext:
    return container.auth_service.upsert_user(user_id, request)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    request: AdminPasswordResetRequest,
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        container.auth_service.admin_reset_password(user_id, request)
        return {"updated": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    current_user: UserContext = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        container.auth_service.delete_user(current_user, user_id)
        return {"deleted": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/roles", response_model=list[RoleRecord])
def list_roles(container: AppContainer = Depends(get_container)) -> list[RoleRecord]:
    return container.auth_service.list_roles()


@router.put("/roles/{role_name}", response_model=RoleRecord)
def upsert_role(
    role_name: str,
    request: RoleUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> RoleRecord:
    return container.auth_service.upsert_role(role_name, request)


@router.get("/eval/cases", response_model=EvaluationCaseCollection)
def list_evaluation_cases(container: AppContainer = Depends(get_container)) -> EvaluationCaseCollection:
    return container.evaluation_service.list_cases()


@router.post("/eval/cases", response_model=EvaluationCase)
def create_evaluation_case(
    request: EvaluationCaseUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationCase:
    return container.evaluation_service.create_case(request.case)


@router.post("/eval/cases/{case_id}/replay", response_model=EvaluationReplayResult)
def replay_evaluation_case(
    case_id: str,
    request: EvaluationReplayRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationReplayResult:
    try:
        return container.evaluation_service.replay_case(case_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evaluation case not found") from exc


@router.get("/eval/runs", response_model=list[EvaluationRunRecord])
def list_evaluation_runs(container: AppContainer = Depends(get_container)) -> list[EvaluationRunRecord]:
    return container.evaluation_service.list_runs()


@router.get("/eval/summary", response_model=EvaluationSummary)
def get_evaluation_summary(
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> EvaluationSummary:
    return container.evaluation_service.summarize_runs(limit=limit)


@router.post("/eval/run", response_model=EvaluationRunRecord)
def run_evaluation(
    request: EvaluationRunRequest,
    container: AppContainer = Depends(get_container),
) -> EvaluationRunRecord:
    return container.evaluation_service.run(request)
