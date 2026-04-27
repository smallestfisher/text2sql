from __future__ import annotations

from collections import defaultdict
from queue import Queue

from backend.app.models.progress import ProgressEvent


class ProgressService:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Queue[ProgressEvent | None]]] = defaultdict(list)

    def subscribe(self, trace_id: str) -> Queue[ProgressEvent | None]:
        queue: Queue[ProgressEvent | None] = Queue()
        self._subscribers[trace_id].append(queue)
        return queue

    def unsubscribe(self, trace_id: str, queue: Queue[ProgressEvent | None]) -> None:
        queues = self._subscribers.get(trace_id)
        if not queues:
            return
        try:
            queues.remove(queue)
        except ValueError:
            return
        if not queues:
            self._subscribers.pop(trace_id, None)

    def publish(self, event: ProgressEvent) -> None:
        for queue in list(self._subscribers.get(event.trace_id, [])):
            queue.put(event)

    def complete(self, trace_id: str) -> None:
        for queue in list(self._subscribers.get(trace_id, [])):
            queue.put(None)
