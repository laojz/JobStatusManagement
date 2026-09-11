"""Deterministic Phase 1 command-router tests."""

from jobs_status_manager.application_core.domain import ActionResolution
from jobs_status_manager.application_core.router import RouterResultKind, route_command


def test_router_parses_confirm_and_reject_codes() -> None:
    """Chinese commands resolve to a typed action and optional code."""
    confirmed = route_command(" 确认 pa-7k3m ")
    rejected = route_command("拒绝")
    assert confirmed.kind is RouterResultKind.RESOLVED
    assert confirmed.resolution is ActionResolution.CONFIRM
    assert confirmed.confirmation_code == "PA-7K3M"
    assert rejected.resolution is ActionResolution.REJECT
    assert rejected.confirmation_code is None


def test_router_leaves_ordinary_text_for_agent_layers() -> None:
    """Non-command text never becomes a write operation."""
    assert route_command("把腾讯标记为面试").kind is RouterResultKind.NOT_A_COMMAND
