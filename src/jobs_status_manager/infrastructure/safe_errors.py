"""Bounded, secret-safe external error summaries."""

from __future__ import annotations

import re
from typing import Final

MAX_SAFE_ERROR_CHARS: Final = 2000
_BEARER_PATTERN: Final = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_ASSIGNMENT_PATTERN: Final = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|credential|key|password|secret|token)\s*[=:]\s*)[^\s&;,]+"
)
_URL_QUERY_PATTERN: Final = re.compile(r"(?i)(https?://[^\s<>]+?)(\?[^\s<>#]*)")
_SENSITIVE_QUERY_PATTERN: Final = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|authorization|credential|key|password|secret|token)\s*=)[^&#\s]+"
)
_BODY_PATTERN: Final = re.compile(
    r"(?i)(\b(?:body|content|data|detail|message|payload|response)\s*[=:]\s*).+"
)


def _redact_url(match: re.Match[str]) -> str:
    """Redact sensitive query values while preserving the URL host/path."""
    query = _SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", match.group(2))
    return f"{match.group(1)}{query}"


def safe_external_error(error: BaseException) -> str:
    """Return a bounded error type and redacted short reason."""
    reason = str(error) or "no provider message"
    reason = _URL_QUERY_PATTERN.sub(_redact_url, reason)
    reason = _BEARER_PATTERN.sub(r"\1[REDACTED]", reason)
    reason = _ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", reason)
    reason = _BODY_PATTERN.sub(r"\1[REDACTED]", reason)
    return f"{type(error).__name__}: {reason}"[:MAX_SAFE_ERROR_CHARS]
