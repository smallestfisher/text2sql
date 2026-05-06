from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import threading

from backend.app.core.exceptions import ClientCancelledError


@dataclass
class CancellationToken:
    _event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reason: str = "client cancelled request"
    _cancelled_at: datetime | None = None

    def cancel(self, reason: str = "client cancelled request") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._cancelled_at = datetime.utcnow()
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def cancelled_at(self) -> datetime | None:
        return self._cancelled_at

    def raise_if_cancelled(self, stage: str | None = None) -> None:
        if not self.cancelled:
            return
        if stage:
            raise ClientCancelledError(f"{self._reason} during {stage}")
        raise ClientCancelledError(self._reason)
