from __future__ import annotations

from contextvars import ContextVar
import logging
from logging.config import dictConfig


request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


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
                    "format": "%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s trace_id=%(trace_id)s] %(message)s",
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
