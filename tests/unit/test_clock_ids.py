"""Deterministic clock and ID tests."""

from uuid import UUID

import pytest

from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator


def test_fake_clock_and_ids_are_deterministic(
    fake_clock: FakeClock, ids: DeterministicIdGenerator
) -> None:
    """Fakes return configured values predictably."""
    before = fake_clock.now()
    fake_clock.advance(60)
    assert (fake_clock.now() - before).total_seconds() == 60
    assert ids.new_id() == UUID("00000000-0000-0000-0000-000000000001")


def test_deterministic_ids_fail_when_exhausted() -> None:
    """Exhaustion is explicit rather than silently random."""
    generator = DeterministicIdGenerator([])
    with pytest.raises(RuntimeError, match="sequence exhausted"):
        generator.new_id()
