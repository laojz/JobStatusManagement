"""Deterministic confirmation and rejection command router."""

import re
from dataclasses import dataclass
from enum import StrEnum, unique

from jobs_status_manager.application_core.domain import ActionResolution


@unique
class RouterResultKind(StrEnum):
    """Classification of a routed command."""

    NOT_A_COMMAND = "NOT_A_COMMAND"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID_CODE = "INVALID_CODE"


@dataclass(frozen=True, slots=True)
class RoutedCommand:
    """Parsed deterministic command."""

    kind: RouterResultKind
    resolution: ActionResolution | None = None
    confirmation_code: str | None = None
    message: str = ""


_COMMAND = re.compile(r"^(确认|拒绝)(?:\s+(PA-[A-Z0-9]{4}))?$")


def route_command(text: str) -> RoutedCommand:
    """Parse a supported Chinese confirmation command without an LLM."""
    match = _COMMAND.fullmatch(" ".join(text.strip().split()).upper())
    if match is None:
        return RoutedCommand(RouterResultKind.NOT_A_COMMAND, message="not a confirmation command")
    resolution = ActionResolution.CONFIRM if match.group(1) == "确认" else ActionResolution.REJECT
    return RoutedCommand(
        kind=RouterResultKind.RESOLVED,
        resolution=resolution,
        confirmation_code=match.group(2),
        message="confirmation command parsed",
    )
