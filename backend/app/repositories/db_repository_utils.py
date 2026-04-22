from __future__ import annotations

from datetime import datetime
import json


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def json_loads(payload: str | None, default):
    if not payload:
        return default
    return json.loads(payload)


def as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"unsupported datetime value: {value!r}")
