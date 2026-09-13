import base64
import json

import pytest

from jobs_status_manager.infrastructure.adapters.qq_imap import (
    Cursor,
    CursorResetReason,
    IMAPCursorError,
    classify_cursor,
    decode_cursor,
    encode_cursor,
)


def test_cursor_round_trip_is_canonical_and_bounded() -> None:
    cursor = Cursor(uidvalidity=123456, uid=7890)

    encoded = encode_cursor(cursor)

    assert decode_cursor(encoded) == cursor
    assert encoded == "imap-v1.eyJ2IjoxLCJ1aWR2YWxpZGl0eSI6MTIzNDU2LCJ1aWQiOjc4OTB9"
    assert len(encoded.encode("utf-8")) <= 255


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("legacy-provider-id", CursorResetReason.LEGACY),
        ("imap-v1.not-base64", CursorResetReason.INVALID),
        (
            "imap-v1." + base64.urlsafe_b64encode(json.dumps([1]).encode()).decode().rstrip("="),
            CursorResetReason.INVALID,
        ),
        (
            "imap-v1."
            + base64.urlsafe_b64encode(json.dumps({"v": 2, "uidvalidity": 1, "uid": 1}).encode())
            .decode()
            .rstrip("="),
            CursorResetReason.VERSION,
        ),
    ],
)
def test_invalid_cursor_is_classified_for_baseline_reset(
    raw: str, reason: CursorResetReason
) -> None:
    result = classify_cursor(raw, current_uidvalidity=7, current_max_uid=10)

    assert result.reset is True
    assert result.reason is reason


def test_cursor_uidvalidity_change_and_uid_regression_reset() -> None:
    changed = classify_cursor(
        encode_cursor(Cursor(uidvalidity=1, uid=4)), current_uidvalidity=2, current_max_uid=10
    )
    regressed = classify_cursor(
        encode_cursor(Cursor(uidvalidity=2, uid=11)), current_uidvalidity=2, current_max_uid=10
    )

    assert changed.reason is CursorResetReason.UIDVALIDITY_CHANGED
    assert regressed.reason is CursorResetReason.UID_REGRESSION


def test_decode_cursor_rejects_negative_values_without_payload_in_error() -> None:
    payload = base64.urlsafe_b64encode(b'{"v":1,"uidvalidity":-1,"uid":2}').decode().rstrip("=")

    with pytest.raises(IMAPCursorError) as caught:
        decode_cursor("imap-v1." + payload)

    assert str(caught.value) == "IMAP cursor is invalid"
