from __future__ import annotations

from contextvars import ContextVar
import logging
from logging.config import dictConfig


request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


class CompactFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "request_id") and hasattr(record, "trace_id"):
            context_parts = []
            if record.request_id != "-":
                context_parts.append(f"req={record.request_id}")
            if record.trace_id != "-":
                context_parts.append(f"trace={record.trace_id}")
            record.context = f" {' '.join(context_parts)}" if context_parts else ""
        else:
            record.context = ""
        return super().format(record)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.trace_id = trace_id_var.get()
        return True


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def clear_request_id() -> None:
    request_id_var.set("-")


def set_trace_id(trace_id: str) -> None:
    trace_id_var.set(trace_id)


def clear_trace_id() -> None:
    trace_id_var.set("-")


def configure_logging(log_level: str = "INFO") -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {
                    "()": "backend.app.logging_config.RequestContextFilter",
                }
            },
            "formatters": {
                "standard": {
                    "()": "backend.app.logging_config.CompactFormatter",
                    "format": "%(asctime)s %(levelname).1s %(name)s%(context)s | %(message)s",
                    "datefmt": "%H:%M:%S",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": log_level,
                    "formatter": "standard",
                    "filters": ["request_context"],
                }
            },
            "root": {
                "level": log_level,
                "handlers": ["console"],
            },
        }
    )
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
