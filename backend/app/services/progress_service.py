from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
import os
from queue import Empty, Queue
import threading

from backend.app.models.progress import ProgressEvent


@dataclass
class ProgressSubscription:
    queue: Queue[ProgressEvent | None]
    read_fd: int
    write_fd: int
    lock: threading.Lock
    closed: bool = False

    @classmethod
    def create(cls) -> "ProgressSubscription":
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        return cls(
            queue=Queue(),
            read_fd=read_fd,
            write_fd=write_fd,
            lock=threading.Lock(),
        )

    async def get(self) -> ProgressEvent | None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                item = self.queue.get_nowait()
                self._drain_notifications()
                return item
            except Empty:
                if self.closed:
                    return None
            future = loop.create_future()

            def on_readable() -> None:
                if not future.done():
                    future.set_result(None)

            loop.add_reader(self.read_fd, on_readable)
            try:
                await future
            finally:
                loop.remove_reader(self.read_fd)

    def push(self, item: ProgressEvent | None) -> None:
        with self.lock:
            if self.closed:
                return
            self.queue.put(item)
            try:
                os.write(self.write_fd, b"\0")
            except (BlockingIOError, InterruptedError):
                # Queue already holds the event; a lost extra wake byte is fine.
                pass
            except OSError:
                self.closed = True

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            os.close(self.read_fd)
            os.close(self.write_fd)

    def _drain_notifications(self) -> None:
        while True:
            try:
                chunk = os.read(self.read_fd, 4096)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk or len(chunk) < 4096:
                break


class ProgressService:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[ProgressSubscription]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, trace_id: str) -> ProgressSubscription:
        subscription = ProgressSubscription.create()
        with self._lock:
            self._subscribers[trace_id].append(subscription)
        return subscription

    def unsubscribe(self, trace_id: str, subscription: ProgressSubscription) -> None:
        with self._lock:
            subscriptions = self._subscribers.get(trace_id)
            if not subscriptions:
                return
            remaining = [item for item in subscriptions if item is not subscription]
            if len(remaining) == len(subscriptions):
                return
            if remaining:
                self._subscribers[trace_id] = remaining
            else:
                self._subscribers.pop(trace_id, None)
        subscription.close()

    def publish(self, event: ProgressEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(event.trace_id, []))
        self._dispatch(subscribers, event)

    def complete(self, trace_id: str) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(trace_id, []))
        self._dispatch(subscribers, None)

    def _dispatch(
        self,
        subscribers: list[ProgressSubscription],
        item: ProgressEvent | None,
    ) -> None:
        for subscriber in subscribers:
            subscriber.push(item)
