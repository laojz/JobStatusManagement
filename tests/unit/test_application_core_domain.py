"""Phase 1 domain unit tests."""

import pytest
from pydantic import ValidationError

from jobs_status_manager.application_core.domain import (
    ApplicationStatus,
    StatusUpdateArguments,
    normalize_key,
    proposal_fingerprint,
    status_changed,
)


def test_normalize_key_preserves_chinese_and_normalizes_text() -> None:
    """Equivalent display forms produce one exact key."""
    assert normalize_key("  Ｔｅｎｃｅｎｔ　 后端开发  ") == "tencent 后端开发"  # noqa: RUF001
    assert normalize_key("腾讯，后端") == normalize_key("腾讯, 后端")  # noqa: RUF001
    assert normalize_key("  腾讯\n后端  ") == "腾讯 后端"


def test_interview_requires_positive_round() -> None:
    """Interview status rejects absent and non-positive rounds."""
    with pytest.raises(ValidationError):
        StatusUpdateArguments(
            company="腾讯", department=None, position="后端", status=ApplicationStatus.INTERVIEW
        )
    with pytest.raises(ValidationError):
        StatusUpdateArguments(
            company="腾讯",
            department=None,
            position="后端",
            status=ApplicationStatus.INTERVIEW,
            interview_round=0,
        )


def test_non_interview_rejects_round_and_fingerprint_is_deterministic() -> None:
    """Non-interview states have no round and equivalent proposals share a fingerprint."""
    with pytest.raises(ValidationError):
        StatusUpdateArguments(
            company="腾讯",
            department=None,
            position="后端",
            status=ApplicationStatus.APPLIED,
            interview_round=1,
        )
    first = StatusUpdateArguments(
        company=" 腾讯 ", department="", position="后端", status=ApplicationStatus.APPLIED
    )
    second = StatusUpdateArguments(
        company="腾讯", department=None, position="后端", status=ApplicationStatus.APPLIED
    )
    assert proposal_fingerprint(first) == proposal_fingerprint(second)


def test_status_changed_includes_round_changes() -> None:
    """An interview round change is a business change even when status is stable."""
    assert status_changed(ApplicationStatus.INTERVIEW, 1, ApplicationStatus.INTERVIEW, 2)
    assert not status_changed(ApplicationStatus.APPLIED, None, ApplicationStatus.APPLIED, None)
