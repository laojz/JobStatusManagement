"""Direct Alembic command integration tests."""

import os
import subprocess
import sys
from pathlib import Path


def test_direct_alembic_commands_use_explicit_application_database_target(tmp_path: Path) -> None:
    """Direct Alembic commands migrate and inspect only APP_DATABASE_PATH."""
    root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "application.db"
    environment = os.environ.copy()
    environment["APP_DATABASE_PATH"] = str(database_path)
    alembic = [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini")]

    upgrade = subprocess.run(  # noqa: S603
        [*alembic, "upgrade", "head"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    check = subprocess.run(  # noqa: S603
        [*alembic, "check"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    current = subprocess.run(  # noqa: S603
        [*alembic, "current"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert upgrade.returncode == 0, upgrade.stderr
    assert check.returncode == 0, check.stderr
    assert current.returncode == 0, current.stderr
    assert "0009_tool_call_provider_metadata (head)" in current.stdout
    assert database_path.exists()
    assert not (tmp_path / "jobs_status_alembic.db").exists()
