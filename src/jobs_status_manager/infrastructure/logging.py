"""Secret-safe structured logging setup."""

import logging
import re
from typing import Final

import structlog
from structlog.typing import EventDict, WrappedLogger

SENSITIVE_KEY_PATTERN: Final = re.compile(r"(key|password|token|secret|credential)", re.IGNORECASE)


def redact_event(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Redact secret-like structured logging fields."""
    return {
        key: "[REDACTED]" if SENSITIVE_KEY_PATTERN.search(key) else value
        for key, value in event_dict.items()
    }


def configure_logging(level: str) -> None:
    """Configure process logging once at startup."""
    logging.basicConfig(level=level.upper(), format="%(message)s", force=True)
    structlog.configure(
        processors=[redact_event, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
