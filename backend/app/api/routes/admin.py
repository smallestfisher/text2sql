from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from backend.app.api.dependencies import get_container, get_current_user, require_admin_user, reset_container
from backend.app.core.container import AppContainer, BUSINESS_SQL_DIALECT
from backend.app.core.settings import (
    EDITABLE_SPECS,
    FIELD_SPECS,
    SPEC_BY_ENV,
    Settings,
)
from backend.app.services.database_connector import DatabaseConnector
from backend.app.models.admin import (
    AdminDashboardResponse,
    AdminMetricChangeRecord,
    AdminMetricsSummary,
    ConfigCollectionResponse,
    ConfigFieldRecord,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    ExampleCollectionResponse,
    ExampleMutationResponse,
    MetadataDocument,
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
from backend.app.models.data_source import (
    DataSourceCollectionResponse,
    DataSourceCreateRequest,
    DataSourceRecord,
    SchemaSyncRequest,
    SchemaSyncResponse,
)
from backend.app.models.trace import TraceRecord
from backend.app.services.oracle_schema_introspector import merge_tables_metadata


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_user)],
)

logger = logging.getLogger(__name__)
MAX_DASHBOARD_WORKERS = 6


class MetadataUpdateRequest(BaseModel):
    content: dict | list | str


class ExampleUpsertRequest(BaseModel):
    example: dict


@router.get("/data-sources", response_model=DataSourceCollectionResponse)
def list_data_sources(
    workspace_id: str | None = None,
    domain_id: str | None = None,
    container: AppContainer = Depends(get_container),
) -> DataSourceCollectionResponse:
    records = container.data_source_repository.list(
        workspace_id=workspace_id,
        domain_id=domain_id,
    )
    return DataSourceCollectionResponse(data_sources=records, count=len(records))


@router.post("/data-sources", response_model=DataSourceRecord)
def create_data_source(
    request: DataSourceCreateRequest,
    container: AppContainer = Depends(get_container),
) -> DataSourceRecord:
    try:
        return container.data_source_repository.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/data-sources/{data_source_id}/sync", response_model=SchemaSyncResponse)
def sync_data_source_schema(
    data_source_id: str,
    request: SchemaSyncRequest,
    container: AppContainer = Depends(get_container),
) -> SchemaSyncResponse:
    record = container.data_source_repository.get(data_source_id)
    database_url = container.data_source_repository.get_database_url(data_source_id)
    if record is None or database_url is None:
        raise HTTPException(status_code=404, detail="data source not found")

    schemas = request.schemas or record.schemas
    container.data_source_repository.update_sync_result(data_source_id, status="syncing")
    try:
        result = container.oracle_schema_introspector.inspect(
            database_url,
            schemas,
            include_views=request.include_views,
        )
        scoped_tables = {
            name: {
                **metadata,
                "workspace_id": record.workspace_id,
                "domain_id": record.domain_id,
                "data_source_id": record.id,
                "dialect": "oracle",
            }
            for name, metadata in result["tables_metadata"].items()
        }
        existing = container.metadata_service.get_document("tables_metadata").content
        existing_doc = dict(existing) if isinstance(existing, dict) else {}
        # Three-way merge: physical facts are refreshed, human-authored business
        # content (description / column annotations / time_fields / relationships)
        # is never overwritten. Drift is surfaced as warnings for the UI.
        merged, merge_warnings = merge_tables_metadata(
            existing_doc, scoped_tables, data_source_id
        )
        container.metadata_service.update_document(
            "tables_metadata",
            merged,
            retrieval_service=container.retrieval_service,
        )
        updated = container.data_source_repository.update_sync_result(
            data_source_id, status="ready"
        )
    except Exception as exc:
        container.data_source_repository.update_sync_result(
            data_source_id, status="error", error=str(exc)
        )
        raise HTTPException(status_code=400, detail=f"Oracle Schema 同步失败: {exc}") from exc

    return SchemaSyncResponse(
        data_source=updated,
        table_count=result["table_count"],
        column_count=result["column_count"],
        relationship_count=result["relationship_count"],
        tables_metadata=merged,
        warnings=list(result.get("warnings", [])) + merge_warnings,
    )


class ExampleBulkUpsertRequest(BaseModel):
    examples: list[dict]
    replace_existing: bool = False


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
            container.metadata_service.overview,
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
            container.vector_retriever.health,
            _empty_vector_retrieval_status(),
        ),
        "retrieval_corpus": (
            container.retrieval_service.health,
            _empty_retrieval_corpus_status(),
        ),
        "sql_ast": (container.sql_ast_validator.health, None),
    }
    results = _runtime_probes(probes)
    return {probe_name: results[probe_name] for probe_name in probes}


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


@router.get("/metadata/overview", response_model=MetadataOverview)
def metadata_overview(container: AppContainer = Depends(get_container)) -> MetadataOverview:
    return container.metadata_service.overview()


@router.get("/metadata/documents")
def list_metadata_documents(container: AppContainer = Depends(get_container)) -> dict:
    return {"documents": container.metadata_service.list_documents()}


@router.get("/metadata/documents/{name}", response_model=MetadataDocument)
def get_metadata_document(name: str, container: AppContainer = Depends(get_container)) -> MetadataDocument:
    try:
        return container.metadata_service.get_document(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="metadata document not found") from exc


@router.put("/metadata/documents/{name}", response_model=MetadataDocument)
def update_metadata_document(
    name: str,
    request: MetadataUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> MetadataDocument:
    try:
        return container.metadata_service.update_document(
            name,
            request.content,
            retrieval_service=container.retrieval_service,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="metadata document not found") from exc


@router.post("/metadata/reload")
def reload_metadata(container: AppContainer = Depends(get_container)) -> dict:
    new_container = reset_container()
    summary = new_container.metadata_service.overview()
    return {
        "semantic_version": summary.semantic_version,
        "reloaded": True,
    }


def _config_fields(container: AppContainer) -> list[ConfigFieldRecord]:
    """Render every settings field with its current effective value and where
    that value came from (override row / env / default)."""
    try:
        overrides = container.app_config_repository.read_all()
    except Exception:
        logger.warning("failed to read app_config overrides for listing", exc_info=True)
        overrides = {}
    records: list[ConfigFieldRecord] = []
    for spec in FIELD_SPECS:
        override_raw = overrides.get(spec.env) if spec.editable else None
        env_raw = os.getenv(spec.env)
        if override_raw is not None and override_raw.strip() != "":
            source = "override"
        elif env_raw is not None and env_raw.strip() != "":
            source = "env"
        else:
            source = "default"
        effective = getattr(container.settings, spec.attr, None)
        records.append(
            ConfigFieldRecord(
                name=spec.env,
                group=spec.group,
                type=spec.type,
                editable=spec.editable,
                secret=spec.secret,
                value=None if effective is None else str(effective),
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


@router.put("/config", response_model=ConfigUpdateResponse)
def update_config(
    request: ConfigUpdateRequest,
    container: AppContainer = Depends(get_container),
    _admin: UserContext = Depends(require_admin_user),
) -> ConfigUpdateResponse:
    editable_envs = {spec.env for spec in EDITABLE_SPECS}

    # 1. Validate the requested keys are known + editable.
    unknown = [name for name in request.values if name not in SPEC_BY_ENV]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown config keys: {', '.join(sorted(unknown))}")
    non_editable = [name for name in request.values if name not in editable_envs]
    if non_editable:
        raise HTTPException(
            status_code=400,
            detail=f"config keys are not editable: {', '.join(sorted(non_editable))}",
        )

    try:
        previous_overrides = container.app_config_repository.read_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to read current config: {exc}") from exc

    # 2. Compute the candidate override set (null / blank -> revert to baseline).
    candidate = dict(previous_overrides)
    for name, value in request.values.items():
        if value is None or value.strip() == "":
            candidate.pop(name, None)
        else:
            candidate[name] = value

    # 3. Type-coercion validation before persisting anything.
    try:
        new_settings = Settings.build(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 4. Business DB connectivity check when its effective URL changes: a bad
    #    URL must be rejected at save time, never persisted.
    if new_settings.business_database_url != container.settings.business_database_url:
        probe = DatabaseConnector(
            database_url=new_settings.business_database_url,
            timeout_seconds=new_settings.sql_timeout_seconds,
            sql_dialect=BUSINESS_SQL_DIALECT,
        )
        try:
            health = probe.test_connection(verify_readonly_session_settings=True)
        finally:
            probe.dispose()
        if not health.get("connected"):
            raise HTTPException(
                status_code=400,
                detail=f"business database is not reachable with the new settings: "
                f"{health.get('error') or 'connection failed'}",
            )

    # 5. Persist: upsert non-blank, delete reverted keys.
    repo = container.app_config_repository
    for name, value in request.values.items():
        try:
            if value is None or value.strip() == "":
                repo.delete(name)
            else:
                repo.upsert(name, value)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to persist {name}: {exc}") from exc

    # 6. Guarded rebuild. If it fails, roll the override rows back to their prior
    #    state so a restart won't load a broken config, then surface the error.
    try:
        new_container = reset_container()
    except Exception as exc:
        _restore_overrides(repo, previous_overrides, candidate)
        raise HTTPException(
            status_code=500,
            detail=f"config saved but container rebuild failed and was rolled back: {exc}",
        ) from exc

    return ConfigUpdateResponse(
        updated=True,
        reloaded=True,
        fields=_config_fields(new_container),
    )


def _restore_overrides(repo, previous: dict[str, str], attempted: dict[str, str]) -> None:
    """Best-effort restore of app_config rows to ``previous`` after a failed
    rebuild. Removes keys that weren't there before and re-writes prior values."""
    try:
        for name in set(attempted) | set(previous):
            if name in previous:
                repo.upsert(name, previous[name])
            else:
                repo.delete(name)
    except Exception:
        logger.error("failed to roll back app_config after rebuild failure", exc_info=True)


@router.get("/examples", response_model=ExampleCollectionResponse)
def list_examples(container: AppContainer = Depends(get_container)) -> ExampleCollectionResponse:
    return container.metadata_service.list_examples(container.retrieval_service)


@router.post("/examples", response_model=ExampleMutationResponse)
def create_example(
    request: ExampleUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleMutationResponse:
    try:
        return container.metadata_service.create_example(
            request.example,
            retrieval_service=container.retrieval_service,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/examples/{example_id}", response_model=ExampleMutationResponse)
def update_example(
    example_id: str,
    request: ExampleUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleMutationResponse:
    try:
        return container.metadata_service.update_example(
            example_id,
            request.example,
            retrieval_service=container.retrieval_service,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="example not found") from exc


@router.post("/examples/bulk", response_model=ExampleCollectionResponse)
def bulk_upsert_examples(
    request: ExampleBulkUpsertRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleCollectionResponse:
    try:
        return container.metadata_service.bulk_upsert_examples(
            request.examples,
            retrieval_service=container.retrieval_service,
            replace_existing=request.replace_existing,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    return container.retrieval_service.prewarm_vector_index()


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


@router.post("/runtime/query-logs/{trace_id}/materialize-example", response_model=ExampleMutationResponse)
def materialize_runtime_query_log_as_example(
    trace_id: str,
    request: RuntimeQueryLogMaterializeExampleRequest,
    container: AppContainer = Depends(get_container),
) -> ExampleMutationResponse:
    try:
        example = container.evaluation_service.materialize_trace_as_example(
            trace_id,
            example_id=request.example_id,
            scenario=request.scenario,
            coverage_tags=request.coverage_tags,
            notes=request.notes,
        )
        return container.metadata_service.materialize_example(
            example,
            retrieval_service=container.retrieval_service,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="query log not found") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
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
