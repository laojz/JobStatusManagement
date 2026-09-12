from dataclasses import FrozenInstanceError, replace

import pytest

from jobs_status_manager.agent.contracts import (
    ProviderError,
    ProviderErrorKind,
    ReplyMode,
    ReplyTarget,
    ReplyTargetError,
)
from jobs_status_manager.infrastructure.adapters.fakes import FakeQQGateway


def test_passive_target_requires_provider_reply_metadata() -> None:
    target = ReplyTarget(
        mode=ReplyMode.PASSIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
        message_id="message",
        event_id="event",
        msg_seq=3,
    )

    assert target.mode is ReplyMode.PASSIVE
    assert target.provider_name == "qq"
    assert target.message_id == "message"
    with pytest.raises(FrozenInstanceError):
        target.__setattr__("target_id", "other")


@pytest.mark.parametrize(
    "field_name",
    ["provider_name", "provider_scope", "target_id", "message_id", "event_id"],
)
def test_reply_target_rejects_whitespace_only_identifiers(field_name: str) -> None:
    target = ReplyTarget(
        mode=ReplyMode.PASSIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
        message_id="message",
        event_id="event",
    )

    with pytest.raises(ReplyTargetError, match=r"(identifiers|required)"):
        replace(target, **{field_name: " \t"})


def test_proactive_target_rejects_passive_only_fields() -> None:
    with pytest.raises(ValueError, match="proactive reply target"):
        ReplyTarget(
            mode=ReplyMode.PROACTIVE,
            provider_name="qq",
            provider_scope="c2c",
            target_id="openid",
            message_id="message",
            event_id=None,
        )


def test_provider_error_distinguishes_definite_and_ambiguous_outcomes() -> None:
    definite = ProviderError(
        kind=ProviderErrorKind.DEFINITE,
        provider_name="qq",
        code="unauthorized",
    )
    ambiguous = ProviderError(
        kind=ProviderErrorKind.AMBIGUOUS,
        provider_name="qq",
        code="timeout",
    )

    assert definite.kind is ProviderErrorKind.DEFINITE
    assert ambiguous.kind is ProviderErrorKind.AMBIGUOUS
    assert str(ambiguous) == "qq provider error timeout (ambiguous)"


def test_fake_qq_gateway_records_explicit_passive_and_proactive_modes() -> None:
    gateway = FakeQQGateway()
    passive = ReplyTarget(
        mode=ReplyMode.PASSIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
        message_id="message",
        event_id="event",
    )
    proactive = ReplyTarget(
        mode=ReplyMode.PROACTIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
    )

    gateway.deliver(passive, "passive")
    gateway.deliver(proactive, "proactive")

    assert gateway.calls == [
        ("deliver", ("PASSIVE", "qq", "c2c", "openid", "message", "event", "", "passive")),
        ("deliver", ("PROACTIVE", "qq", "c2c", "openid", "", "", "", "proactive")),
    ]


def test_fake_qq_gateway_records_media_operations_without_network() -> None:
    gateway = FakeQQGateway()
    target = ReplyTarget(
        mode=ReplyMode.PROACTIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
    )

    file_result = gateway.send_file(target, "report.txt", "text/plain", b"report")
    image_result = gateway.send_image(target, "image/png", b"png")

    assert file_result.success is True
    assert image_result.provider_message_id == "fake-message"
    assert gateway.calls == [
        ("send_file", ("qq", "c2c", "openid", "report.txt", "text/plain", "6")),
        ("send_image", ("qq", "c2c", "openid", "image/png", "3")),
    ]
