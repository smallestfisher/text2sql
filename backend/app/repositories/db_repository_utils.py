from __future__ import annotations

from datetime import datetime, timezone
import json


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def json_loads(payload: str | None, default):
    if not payload:
        return default
    return json.loads(payload)


def as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip().replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    raise TypeError(f"unsupported datetime value: {value!r}")
