"""Runtime process CLI command."""

import typer
import uvicorn

from jobs_status_manager.application.lifecycle import create_app
from jobs_status_manager.bootstrap.cli_support import load_settings, project_root
from jobs_status_manager.infrastructure.database.migrations import upgrade_database


def register(app: typer.Typer) -> None:
    """Register the single-process runtime command."""

    @app.command()
    def run() -> None:
        """Start the single-process foundation HTTP lifecycle."""
        settings = load_settings()
        root = project_root()
        upgrade_database(root, f"sqlite:///{settings.database_path}")
        uvicorn.run(
            create_app(settings, root),
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )
