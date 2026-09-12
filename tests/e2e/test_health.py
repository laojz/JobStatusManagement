"""Foundation HTTP lifecycle tests."""

from pathlib import Path

from starlette.testclient import TestClient

from jobs_status_manager.application.lifecycle import create_app
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.database.migrations import upgrade_database


def test_health_startup_and_shutdown(settings: AppSettings) -> None:
    """Health reports ready only after the schema exists and lifecycle closes cleanly."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    app = create_app(settings, root)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "phase": "6",
            "database": "ok",
            "schema_version": "0008_qq_reply_targets",
            "readiness": "ready",
            "durable_tasks": "ok",
            "failed_tasks": 0,
            "stale_tasks": 0,
        }
