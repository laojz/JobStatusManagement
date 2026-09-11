"""Shared isolated test fixtures."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    """Provide credential-free settings backed by a temporary directory."""
    return AppSettings(
        database_path=tmp_path / "data" / "jobs.db",
        data_dir=tmp_path / "data",
        bootstrap_user_external_key="test-user",
        bootstrap_user_display_name="Test User",
        bootstrap_mail_provider="local",
        bootstrap_mail_account_key="test-account",
        bootstrap_mail_display_name="Test Mail",
        qq_user_openid="openid-1",
    )


@pytest.fixture
def database(settings: AppSettings) -> Iterator[Database]:
    """Provide an isolated file-backed database."""
    value = Database(settings.database_path)
    yield value
    value.dispose()


@pytest.fixture
def fake_clock() -> FakeClock:
    """Provide a stable UTC clock."""
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def ids() -> DeterministicIdGenerator:
    """Provide deterministic identity IDs."""
    return DeterministicIdGenerator(
        [
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000002"),
        ]
    )
