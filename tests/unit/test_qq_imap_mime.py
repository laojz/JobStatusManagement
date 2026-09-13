from datetime import UTC, datetime
from email.message import EmailMessage

import pytest

from jobs_status_manager.infrastructure.adapters.qq_imap import (
    IMAPProtocolError,
    message_to_envelope,
)
from jobs_status_manager.mail import MAX_BODY_CHARS


def _message() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "=?utf-8?b?5Y+X6YKA?="
    message["From"] = "招聘组 <recruit@example.com>"
    message["To"] = "candidate@example.com, Candidate <candidate@example.com>"
    message["Cc"] = "copy@example.com"
    message["Date"] = "Tue, 10 Sep 2024 12:00:00 +0800"
    message.set_content("plain\x00\x01\r\nbody")
    message.add_alternative("<p>html fallback</p><script>secret()</script>", subtype="html")
    attachment = EmailMessage()
    attachment.set_content(b"binary payload", maintype="application", subtype="octet-stream")
    attachment.add_header("Content-Disposition", "attachment", filename="private.bin")
    message.make_mixed()
    message.attach(attachment)
    return message


def test_mime_conversion_prefers_plain_and_excludes_attachment() -> None:
    envelope = message_to_envelope(
        _message().as_bytes(),
        provider_message_id="qq-imap-v1:9:10",
        internaldate=datetime(2024, 9, 10, 4, tzinfo=UTC),
    )

    assert envelope.subject == "受邀"
    assert envelope.sender == "招聘组 <recruit@example.com>"
    assert envelope.recipients == ("candidate@example.com", "copy@example.com")
    assert envelope.received_at == datetime(2024, 9, 10, 4, tzinfo=UTC)
    assert envelope.content == "plain\nbody\n"
    assert "binary payload" not in envelope.content


def test_mime_conversion_uses_internaldate_and_bounds_html_fallback() -> None:
    message = EmailMessage()
    message["Subject"] = "HTML"
    message["From"] = "sender@example.com"
    message.add_alternative(
        "<a href='https://tracker.invalid'>" + "x" * 20000 + "</a>", subtype="html"
    )

    envelope = message_to_envelope(
        message.as_bytes(),
        provider_message_id="qq-imap-v1:9:11",
        internaldate=datetime(2024, 9, 10, 4, tzinfo=UTC),
    )

    assert envelope.received_at == datetime(2024, 9, 10, 4, tzinfo=UTC)
    assert len(envelope.content) <= MAX_BODY_CHARS
    assert "https://tracker.invalid" not in envelope.content


def test_mime_conversion_uses_declared_charset_and_rejects_missing_dates() -> None:
    message = EmailMessage()
    message["Subject"] = "Charset"
    message["From"] = "sender@example.com"
    message.set_content("中文正文", charset="gb18030")

    with pytest.raises(IMAPProtocolError):
        message_to_envelope(
            message.as_bytes(), provider_message_id="qq-imap-v1:9:12", internaldate=None
        )

    envelope = message_to_envelope(
        message.as_bytes(),
        provider_message_id="qq-imap-v1:9:12",
        internaldate=datetime(2024, 9, 10, 4, tzinfo=UTC),
    )

    assert "中文正文" in envelope.content
