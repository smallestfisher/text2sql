from __future__ import annotations

import threading
from collections.abc import Callable

from backend.app.core.cancellation import CancellationToken
from backend.app.models.api import ChatRequest, ChatResponse
from backend.app.services.orchestrator import ConversationOrchestrator
from backend.app.services.release_runtime_manager import ReleaseRuntime, ReleaseRuntimeManager
from backend.app.services.session_service import SessionService


class ReleaseAwareOrchestrator:
    """Resolve the session release and delegate to an immutable orchestrator."""

    def __init__(
        self,
        *,
        runtime_manager: ReleaseRuntimeManager,
        session_service: SessionService,
        orchestrator_factory: Callable[[ReleaseRuntime], ConversationOrchestrator],
        audit_service,
    ) -> None:
        self.runtime_manager = runtime_manager
        self.session_service = session_service
        self.orchestrator_factory = orchestrator_factory
        self.audit_service = audit_service
        self._orchestrators: dict[str, ConversationOrchestrator] = {}
        self._lock = threading.RLock()

    def chat(
        self,
        request: ChatRequest,
        trace_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> ChatResponse:
        release_id = self._resolve_release_id(request)
        request.semantic_release_id = release_id
        runtime = self.runtime_manager.get(release_id)
        orchestrator = self._get_orchestrator(runtime)
        return orchestrator.chat(request, trace_id, cancellation_token)

    def _resolve_release_id(self, request: ChatRequest) -> str:
        state_release_id = (
            request.session_state.semantic_release_id
            if request.session_state is not None
            else None
        )
        if request.session_id:
            session = self.session_service.get_session(request.session_id)
            if session is None:
                raise ValueError(f"session not found: {request.session_id}")
            if not session.semantic_release_id:
                raise ValueError("session has no semantic release; create a new session")
            if state_release_id and state_release_id != session.semantic_release_id:
                raise ValueError("session state semantic release does not match the session")
            return session.semantic_release_id
        release_id = state_release_id or self.runtime_manager.active_release_id()
        if not release_id:
            raise RuntimeError("no active semantic release; sync, configure, and publish first")
        return release_id

    def _get_orchestrator(self, runtime: ReleaseRuntime) -> ConversationOrchestrator:
        with self._lock:
            orchestrator = self._orchestrators.get(runtime.release.id)
            if orchestrator is None:
                orchestrator = self.orchestrator_factory(runtime)
                self._orchestrators[runtime.release.id] = orchestrator
            return orchestrator
