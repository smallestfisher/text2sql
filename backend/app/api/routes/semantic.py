from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.dependencies import get_container
from backend.app.core.container import AppContainer
from backend.app.models.api import ChatRequest


router = APIRouter(prefix="/api/semantic", tags=["semantic"])


@router.get("/summary")
def domain_summary(container: AppContainer = Depends(get_container)) -> dict:
    release_id = container.release_runtime_manager.active_release_id()
    if not release_id:
        return {
            "version": None,
            "domains": [],
            "entities": [],
            "metrics": [],
            "tables": [],
            "starter_questions": [],
            "published": False,
        }
    runtime = container.release_runtime_manager.get(release_id)
    examples = [
        str(item.get("question") or "").strip()
        for item in runtime.metadata_registry.examples_template
        if isinstance(item, dict) and str(item.get("question") or "").strip()
    ]
    metrics = []
    for item in runtime.metadata_registry.examples_template:
        if not isinstance(item, dict):
            continue
        for metric in item.get("metrics", []) or []:
            value = str(metric or "").strip()
            if value and value not in metrics:
                metrics.append(value)
    return {
        "version": f"v{runtime.release.version}",
        "domains": runtime.semantic_runtime.subject_domains(),
        "entities": [],
        "metrics": metrics,
        "tables": list(runtime.metadata_registry.tables_metadata.keys()),
        "starter_questions": examples[:8],
        "published": True,
    }


@router.post("/retrieve-preview")
def retrieve_preview(
    request: ChatRequest,
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        runtime = container.release_runtime_manager.get()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    question_context = runtime.question_context_service.build(
        question=request.question,
        session_state=request.session_state,
    )
    retrieval = runtime.retrieval_service.retrieve_text(
        question=question_context.effective_question,
        semantic_brief=question_context.semantic_brief,
        conversation_summary=question_context.conversation_summary,
    )
    return {
        "question_context": question_context.model_dump(),
        "retrieval": retrieval.model_dump(),
    }
