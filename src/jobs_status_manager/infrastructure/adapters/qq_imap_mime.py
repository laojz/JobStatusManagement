"""RFC 5322 and MIME conversion at the QQ IMAP boundary."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from jobs_status_manager.infrastructure.adapters.qq_imap_cursor import IMAPProtocolError
from jobs_status_manager.mail import MAX_BODY_CHARS, MailEnvelope

_MAX_DECODE_BYTES = MAX_BODY_CHARS * 4


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style"}:
            self.hidden += 1
        elif tag.casefold() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag.casefold() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden == 0:
            self.parts.append(data)


def _clean_text(value: str) -> str:
    return "".join(
        char
        for char in value.replace("\r\n", "\n").replace("\r", "\n")
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )[:MAX_BODY_CHARS]


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload[:_MAX_DECODE_BYTES].decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError):
        return payload[:_MAX_DECODE_BYTES].decode(charset, errors="replace")


def _parts(message: Message) -> list[tuple[str, str]]:
    if not message.is_multipart():
        if message.get_content_disposition() == "attachment":
            return []
        content_type = message.get_content_type()
        return (
            [(content_type, _decode_part(message))]
            if content_type in {"text/plain", "text/html"}
            else []
        )
    payload = message.get_payload()
    if isinstance(payload, list):
        children = [part for part in payload if isinstance(part, Message)]
    else:
        children = []
    candidates = [part for child in children for part in _parts(child)]
    if message.get_content_subtype() == "alternative":
        plain = [part for part in candidates if part[0] == "text/plain"]
        return (plain or [part for part in candidates if part[0] == "text/html"])[:1]
    return candidates


def _body(message: Message) -> str:
    selected = _parts(message)
    plain = [value for content_type, value in selected if content_type == "text/plain"]
    values = plain or [value for _, value in selected]
    if not plain and values:
        parser = _HTMLText()
        parser.feed(values[0][:MAX_BODY_CHARS])
        values = ["".join(parser.parts)]
    result = "\n".join(values)
    return _clean_text(result[:MAX_BODY_CHARS])


def _received_at(message: Message, internaldate: datetime | None) -> datetime:
    raw_date = message.get("date")
    if raw_date:
        try:
            parsed = parsedate_to_datetime(str(raw_date))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
        except (TypeError, ValueError, IndexError, OverflowError):
            parsed = None
    if internaldate is not None:
        return (
            internaldate.astimezone(UTC)
            if internaldate.tzinfo
            else internaldate.replace(tzinfo=UTC)
        )
    raise IMAPProtocolError


def _display_address(name: str, address: str) -> str:
    clean_name, clean_address = _clean_text(name), _clean_text(address)
    return f"{clean_name} <{clean_address}>" if clean_name else clean_address


def message_to_envelope(
    raw_message: bytes, *, provider_message_id: str, internaldate: datetime | None
) -> MailEnvelope:
    """Convert one RFC 5322 message without exposing raw payload in errors."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
    except (TypeError, ValueError, IndexError):
        raise IMAPProtocolError from None
    sender_values = getaddresses([str(make_header(decode_header(str(message.get("from", "")))))])
    sender = _display_address(*sender_values[0]) if sender_values and sender_values[0][1] else ""
    recipients: list[str] = []
    headers = [str(make_header(decode_header(str(message.get(name, ""))))) for name in ("to", "cc")]
    for name, address in getaddresses(headers):
        if address and address.casefold() not in {item.casefold() for item in recipients}:
            recipients.append(_display_address(name, address))
    return MailEnvelope(
        provider_message_id=provider_message_id,
        subject=_clean_text(str(message.get("subject", ""))),
        sender=_clean_text(sender),
        recipients=tuple(_clean_text(item) for item in recipients),
        received_at=_received_at(message, internaldate),
        content=_body(message),
    )
