"""Structured JSON logging via structlog — call configure_logging() at startup."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def _drop_color_message(
    logger: Any, method: Any, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """Wire structlog to emit JSON lines on stdout.

    Call once at application / worker startup. Subsequent calls are no-ops
    because structlog caches the configuration.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Forward stdlib logging into structlog so SQLAlchemy/uvicorn logs get JSON.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
