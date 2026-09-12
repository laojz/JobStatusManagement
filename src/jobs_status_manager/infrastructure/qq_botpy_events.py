"""Normalize botpy gateway payloads at the infrastructure boundary."""

from __future__ import annotations

from collections.abc import Mapping

from jobs_status_manager.agent.contracts import QQInboundEvent

SUPPORTED_EVENT_TYPES = frozenset({"C2C_MESSAGE_CREATE", "C2C_FILE_CREATE"})


def normalize_sdk_event(payload: Mapping[str, object]) -> QQInboundEvent | None:
    """Parse one SDK-shaped C2C message payload into the app contract."""
    event_type = payload.get("t")
    data = payload.get("d")
    if not isinstance(event_type, str) or not isinstance(data, Mapping):
        return None
    if event_type not in SUPPORTED_EVENT_TYPES:
        return None
    event_id = _string(payload, "id") or _string(data, "event_id") or _string(data, "id")
    message_id = _string(data, "message_id") or _string(data, "id")
    author = data.get("author")
    user_openid = _string(data, "user_openid")
    if isinstance(author, Mapping):
        user_openid = user_openid or _string(author, "user_openid")
    if event_id is None or message_id is None or user_openid is None:
        return None
    attachment_values = _attachment_values(data)
    if (
        attachment_values is None
        or len(attachment_values) > 1
        or any(not isinstance(value, Mapping) for value in attachment_values)
    ):
        return None
    attachment = (
        attachment_values[0]
        if attachment_values and isinstance(attachment_values[0], Mapping)
        else None
    )
    values: dict[str, str | int | None] = {
        "event_id": event_id,
        "user_openid": user_openid,
        "message_id": message_id,
        "msg_seq": _positive_int(data, "msg_seq"),
        "event_type": "C2C_FILE_CREATE" if attachment is not None else event_type,
        "content": (_string(data, "content") or ("文件上传" if attachment is not None else "消息")),
    }
    if attachment is not None:
        values.update(
            {
                "provider_file_id": _string(attachment, "id") or _string(attachment, "file_id"),
                "filename": _string(attachment, "filename") or _string(attachment, "name"),
                "content_type": _string(attachment, "content_type")
                or _string(attachment, "contentType"),
                "size_bytes": _positive_int(attachment, "size")
                or _positive_int(attachment, "size_bytes"),
                "file_url": _string(attachment, "url"),
            }
        )
    try:
        return QQInboundEvent.model_validate(values)
    except (TypeError, ValueError):
        return None


def _string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _positive_int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def is_valid_unsupported_event(payload: Mapping[str, object]) -> bool:
    """Recognize valid gateway events that this application intentionally ignores."""
    event_type = payload.get("t")
    return (
        isinstance(event_type, str)
        and bool(event_type.strip())
        and event_type not in SUPPORTED_EVENT_TYPES
        and isinstance(payload.get("d"), Mapping)
    )


def _has_multiple_attachments(payload: Mapping[str, object]) -> bool:
    data = payload.get("d")
    if not isinstance(data, Mapping):
        return False
    attachment_values = _attachment_values(data)
    return attachment_values is not None and len(attachment_values) > 1


def _attachment_values(data: Mapping[str, object]) -> list[object] | None:
    attachments: list[object] = []
    for key in ("attachments", "files"):
        if key not in data:
            continue
        values = data.get(key)
        if not isinstance(values, list):
            return None
        attachments.extend(values)
    return attachments
