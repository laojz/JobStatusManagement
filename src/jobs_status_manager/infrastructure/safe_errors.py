"""Bounded, secret-safe external error summaries."""

from __future__ import annotations

import re
from typing import Final

MAX_SAFE_ERROR_CHARS: Final = 2000
_BEARER_PATTERN: Final = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_QUOTED_ASSIGNMENT_PATTERN: Final = re.compile(
    r"""(?i)(["']?\b(?:api[_-]?key|access[_-]?token|authorization|credential|key|password|secret|token)\b["']?\s*[=:]\s*)(["'])(.*?)\2"""
)
_ASSIGNMENT_PATTERN: Final = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|credential|key|password|secret|token)\b\s*[=:]\s*)[^\s&;,}\]\"']+(?:\s+(?!\b(?:api[_-]?key|access[_-]?token|authorization|credential|key|password|secret|token)\b\s*[=:])[^&;,}\]]+)*"
)
_URL_QUERY_PATTERN: Final = re.compile(r"(?i)(https?://[^\s<>]+?)(\?[^\s<>#]*)")
_BODY_PATTERN: Final = re.compile(
    r"(?i)(\b(?:body|content|data|detail|message|payload|response)\s*[=:]\s*).+"
)


def _redact_url(match: re.Match[str]) -> str:
    """Replace the complete URL query while preserving the host and path."""
    return f"{match.group(1)}?[REDACTED]"


def safe_external_error(error: BaseException) -> str:
    """Return a bounded error type and redacted short reason."""
    reason = str(error) or "no provider message"
    reason = _URL_QUERY_PATTERN.sub(_redact_url, reason)
    reason = _BEARER_PATTERN.sub(r"\1[REDACTED]", reason)
    reason = _QUOTED_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]\2", reason)
    reason = _ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", reason)
    reason = _BODY_PATTERN.sub(r"\1[REDACTED]", reason)
    return f"{type(error).__name__}: {reason}"[:MAX_SAFE_ERROR_CHARS]
