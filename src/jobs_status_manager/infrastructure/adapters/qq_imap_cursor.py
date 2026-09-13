"""Versioned QQ IMAP cursor primitives."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

_CURSOR_PREFIX: Final = "imap-v1."
_CURSOR_VERSION: Final = 1
_BASE64_RE: Final = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_CURSOR_BYTES: Final = 255


@dataclass(frozen=True, slots=True)
class Cursor:
    """Persisted UIDVALIDITY and UID high-watermark."""

    uidvalidity: int
    uid: int


class CursorResetReason(StrEnum):
    """Reason a persisted cursor cannot be resumed."""

    INITIAL = "initial"
    LEGACY = "legacy"
    INVALID = "invalid"
    VERSION = "version"
    UIDVALIDITY_CHANGED = "uidvalidity_changed"
    UID_REGRESSION = "uid_regression"


class IMAPPhase(StrEnum):
    """Allowlisted IMAP adapter boundary used in safe diagnostics."""

    CONNECT = "connect"
    LOGIN = "login"
    SELECT = "select"
    UIDVALIDITY = "uidvalidity"
    SEARCH = "search"
    FETCH = "fetch"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class CursorClassification:
    """Cursor selected for this poll and its reset status."""

    cursor: Cursor
    reset: bool
    reason: CursorResetReason | None = None


class IMAPError(Exception):
    """Base class for safe IMAP adapter errors."""

    def __init__(self, phase: IMAPPhase | None = None) -> None:
        """Create an optionally phase-labelled safe adapter error."""
        super().__init__()
        self.phase = phase

    def __str__(self) -> str:
        """Return a stable safe error label."""
        if self.phase is not None:
            return f"{type(self).__name__}: {self.phase.value}"
        return type(self).__name__


class IMAPCursorError(IMAPError):
    """Persisted cursor is malformed or outside the supported schema."""

    def __str__(self) -> str:
        """Return a stable message without the rejected cursor."""
        if self.phase is not None:
            return f"{type(self).__name__}: {self.phase.value}"
        return "IMAP cursor is invalid"


class IMAPProtocolError(IMAPError):
    """The server returned a response that violates the expected IMAP shape."""


class IMAPConfigurationError(IMAPError):
    """Required IMAP configuration is missing or invalid."""


class IMAPAuthenticationError(IMAPError):
    """IMAP credentials were rejected."""


class IMAPTransportError(IMAPError):
    """The IMAP connection or transport failed."""


class IMAPTimeoutError(IMAPError):
    """An IMAP connection or command timed out."""


def encode_cursor(cursor: Cursor) -> str:
    """Encode a non-negative cursor as canonical compact base64url JSON."""
    if cursor.uidvalidity < 0 or cursor.uid < 0:
        raise IMAPCursorError
    payload = json.dumps(
        {"v": _CURSOR_VERSION, "uidvalidity": cursor.uidvalidity, "uid": cursor.uid},
        separators=(",", ":"),
    ).encode("ascii")
    encoded = _CURSOR_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if len(encoded.encode("utf-8")) > _MAX_CURSOR_BYTES:
        raise IMAPCursorError
    return encoded


def decode_cursor(value: str) -> Cursor:
    """Decode and strictly validate one canonical cursor."""
    if not value.startswith(_CURSOR_PREFIX):
        raise IMAPCursorError
    encoded = value.removeprefix(_CURSOR_PREFIX)
    if not encoded or not _BASE64_RE.fullmatch(encoded):
        raise IMAPCursorError
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        data = json.loads(raw.decode("ascii"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise IMAPCursorError from None
    if (
        not isinstance(data, dict)
        or set(data) != {"v", "uidvalidity", "uid"}
        or type(data["v"]) is not int
        or type(data["uidvalidity"]) is not int
        or type(data["uid"]) is not int
        or data["uidvalidity"] < 0
        or data["uid"] < 0
    ):
        raise IMAPCursorError
    cursor = Cursor(uidvalidity=data["uidvalidity"], uid=data["uid"])
    if data["v"] != _CURSOR_VERSION or encode_cursor(cursor) != value:
        raise IMAPCursorError
    return cursor


def classify_cursor(
    value: str | None, *, current_uidvalidity: int, current_max_uid: int
) -> CursorClassification:
    """Classify persisted state and establish a safe baseline when needed."""
    baseline = Cursor(uidvalidity=current_uidvalidity, uid=max(0, current_max_uid))
    if value is None:
        return CursorClassification(cursor=baseline, reset=True, reason=CursorResetReason.INITIAL)
    if not value.startswith(_CURSOR_PREFIX):
        return CursorClassification(cursor=baseline, reset=True, reason=CursorResetReason.LEGACY)
    try:
        cursor = decode_cursor(value)
    except IMAPCursorError:
        reason = (
            CursorResetReason.VERSION
            if _has_unsupported_version(value)
            else CursorResetReason.INVALID
        )
        return CursorClassification(cursor=baseline, reset=True, reason=reason)
    if cursor.uidvalidity != current_uidvalidity:
        return CursorClassification(
            cursor=baseline, reset=True, reason=CursorResetReason.UIDVALIDITY_CHANGED
        )
    if cursor.uid > current_max_uid:
        return CursorClassification(
            cursor=baseline, reset=True, reason=CursorResetReason.UID_REGRESSION
        )
    return CursorClassification(cursor=cursor, reset=False)


def _has_unsupported_version(value: str) -> bool:
    encoded = value.removeprefix(_CURSOR_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        data = json.loads(raw.decode("ascii"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("v") != _CURSOR_VERSION and "v" in data
