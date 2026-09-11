"""Deterministic clock abstractions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Clock contract used by application services."""

    def now(self) -> datetime:
        """Return current UTC time."""
        ...


class SystemClock:
    """UTC wall clock."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""
        return datetime.now(UTC)


@dataclass(slots=True)
class FakeClock:
    """Mutable deterministic clock for tests."""

    current: datetime

    def now(self) -> datetime:
        """Return the configured time."""
        return self.current

    def advance(self, seconds: float) -> None:
        """Advance the clock by seconds."""
        self.current += timedelta(seconds=seconds)
