from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json

from backend.app.models.api import ExecutionResponse
from backend.app.models.auth import UserContext


@dataclass
class CacheEntry:
    value: ExecutionResponse
    expires_at: datetime


class ExecutionCacheService:
    def __init__(self, ttl_seconds: int = 30, max_entries: int = 256) -> None:
        self.ttl_seconds = max(ttl_seconds, 1)
        self.max_entries = max(max_entries, 1)
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()

    def get(self, sql: str, user_context: UserContext | None = None) -> ExecutionResponse | None:
        key = self._cache_key(sql, user_context)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= datetime.now(tz=timezone.utc):
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        cached = entry.value.model_copy(deep=True)
        cached.warnings = list(cached.warnings) + ["execution cache hit"]
        return cached

    def put(self, sql: str, execution: ExecutionResponse, user_context: UserContext | None = None) -> None:
        if not self._cacheable(execution):
            return
        key = self._cache_key(sql, user_context)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=self.ttl_seconds)
        self._entries[key] = CacheEntry(value=execution.model_copy(deep=True), expires_at=expires_at)
        self._entries.move_to_end(key)
        self._evict_if_needed()

    def clear(self) -> None:
        self._entries.clear()

    def _evict_if_needed(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def _cacheable(self, execution: ExecutionResponse) -> bool:
        return execution.executed and execution.status in {"ok", "empty_result", "truncated"}

    def _cache_key(self, sql: str, user_context: UserContext | None) -> str:
        payload = {
            "sql": sql,
            "user_id": user_context.user_id if user_context else None,
        }
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
