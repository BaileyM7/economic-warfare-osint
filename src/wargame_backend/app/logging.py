"""Structlog configuration for the Swarm backend.

Call `configure_logging()` once at application startup (in `lifespan`).

JSON renderer is used in staging/production; console-pretty renderer
is used in development.  A `request_id` key is injected by the
`RequestIDMiddleware` and propagated via a `contextvars`-bound processor.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog
from structlog.types import EventDict, Processor

# Context variable that carries the current request ID.
# Set by RequestIDMiddleware; read by the bound_request_id processor.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the current request ID (empty string if not in a request context)."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID for the current async task / coroutine."""
    _request_id_var.set(request_id)


def _inject_request_id(
    logger: logging.Logger,  # noqa: ARG001
    method: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor: inject `request_id` from the context variable."""
    rid = _request_id_var.get()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def configure_logging(log_level: str = "INFO", environment: str = "development") -> None:
    """Configure structlog for the given environment.

    Call this exactly once at application startup.

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        environment: One of 'development', 'staging', 'production'.
            Non-development environments use JSON rendering.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_request_id,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    is_production = environment in ("staging", "production")

    if is_production:
        # JSON output for log aggregators (Loki, Datadog, CloudWatch, etc.)
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Human-friendly colored output for local dev
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        # Use the stdlib LoggerFactory (not PrintLoggerFactory) because
        # `add_logger_name` reads logger.name, which only stdlib loggers have.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard-library logging so third-party libraries
    # (SQLAlchemy, uvicorn, etc.) flow through structlog's processors.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Quieten noisy libraries in production
    if is_production:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
