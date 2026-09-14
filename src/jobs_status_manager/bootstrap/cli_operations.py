"""Database and durable-task CLI commands."""

from pathlib import Path

import typer

from jobs_status_manager.application.operations import (
    TaskKind,
    backup_database,
    check_integrity,
    list_tasks,
    restore_check,
    retry_task,
)
from jobs_status_manager.bootstrap.cli_support import load_settings, project_root
from jobs_status_manager.bootstrap.service import BootstrapConflictError, bootstrap_identity
from jobs_status_manager.infrastructure.adapters.factory import (
    AdapterConfigurationError,
    create_owned_adapters,
)
from jobs_status_manager.infrastructure.clock import SystemClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import (
    current_revision,
    upgrade_database,
)
from jobs_status_manager.infrastructure.ids import UUIDGenerator
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.index import IndexServices
from jobs_status_manager.knowledge.rebuild import rebuild_index

adapter_factory = create_owned_adapters


def register(app: typer.Typer) -> None:  # noqa: C901, PLR0915
    """Register database and durable-task commands."""

    @app.command()
    def migrate() -> None:
        """Apply all versioned migrations."""
        settings = load_settings()
        root = project_root()
        upgrade_database(root, f"sqlite:///{settings.database_path}")
        typer.echo(f"migrated schema to {current_revision(f'sqlite:///{settings.database_path}')}")

    @app.command()
    def bootstrap() -> None:
        """Migrate and create the configured user/mail identity idempotently."""
        settings = load_settings()
        root = project_root()
        database_url = f"sqlite:///{settings.database_path}"
        upgrade_database(root, database_url)
        database = Database(settings.database_path)
        try:
            result = bootstrap_identity(database, settings, SystemClock(), UUIDGenerator())
        except BootstrapConflictError as error:
            typer.echo(safe_external_error(error), err=True)
            raise typer.Exit(code=2) from error
        finally:
            database.dispose()
        typer.echo(
            f"user={result.user_id} mail_account={result.mail_account_id} created={result.created}"
        )

    @app.command()
    def health() -> None:
        """Print the current database schema readiness."""
        settings = load_settings()
        database = Database(settings.database_path)
        try:
            revision = current_revision(f"sqlite:///{settings.database_path}")
        finally:
            database.dispose()
        if revision is None:
            typer.echo("not_ready: schema_migrations is missing", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"ready phase=6 schema_version={revision}")

    @app.command("failures")
    def failures(
        include_stale: bool = typer.Option(default=False, help="Include stale running tasks"),
    ) -> None:
        """List durable failed tasks without sensitive bodies."""
        settings = load_settings()
        database = Database(settings.database_path)
        try:
            for task in list_tasks(database, include_stale=include_stale):
                typer.echo(
                    f"kind={task.kind.value} id={task.task_id} related={task.related_id or '-'} "
                    f"state={task.state} attempts={task.attempt_count} stale={task.stale} "
                    f"last_error={task.last_error or '-'} next_retry={task.next_retry_at or '-'}"
                )
        finally:
            database.dispose()

    @app.command("integrity-check")
    def integrity_check() -> None:
        """Check the live SQLite database integrity and schema revision."""
        settings = load_settings()
        result = check_integrity(settings.database_path, project_root())
        typer.echo(
            f"integrity={result.integrity} foreign_keys={len(result.foreign_keys)} "
            f"core_tables={len(result.core_tables)} schema_version={result.schema_version or '-'}"
        )
        if not result.ok:
            raise typer.Exit(code=1)

    @app.command("backup")
    def backup(destination: Path) -> None:
        """Create a consistent SQLite backup at DESTINATION."""
        settings = load_settings()
        database = Database(settings.database_path)
        try:
            result = backup_database(database, destination, project_root())
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(safe_external_error(error), err=True)
            raise typer.Exit(code=1) from error
        finally:
            database.dispose()
        typer.echo(
            f"backup={result.destination} integrity={result.integrity.integrity} "
            f"foreign_keys={len(result.integrity.foreign_keys)}"
        )

    @app.command("restore-check")
    def restore_check_command(source: Path) -> None:
        """Validate a backup in an isolated temporary SQLite database."""
        try:
            result = restore_check(source, project_root())
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(safe_external_error(error), err=True)
            raise typer.Exit(code=1) from error
        typer.echo(
            f"restore_check={result.ok} integrity={result.integrity} "
            f"foreign_keys={len(result.foreign_keys)}"
        )
        if not result.ok:
            raise typer.Exit(code=1)

    @app.command("retry")
    def retry(kind: str, task_id: str) -> None:
        """Retry one existing failed task by kind and ID."""
        settings = load_settings()
        database = Database(settings.database_path)
        try:
            result = retry_task(database, TaskKind(kind), task_id, SystemClock(), UUIDGenerator())
        except (ValueError, RuntimeError) as error:
            typer.echo(safe_external_error(error), err=True)
            raise typer.Exit(code=1) from error
        finally:
            database.dispose()
        typer.echo(f"kind={result.kind.value} id={result.task_id} state={result.state}")

    @app.command("rebuild-chroma")
    def rebuild_chroma() -> None:
        """Rebuild Chroma from ACTIVE relational knowledge while the app is stopped."""
        settings = load_settings()
        try:
            resources = adapter_factory(settings)
        except AdapterConfigurationError as error:
            typer.echo(safe_external_error(error), err=True)
            raise typer.Exit(code=1) from error
        if resources is None:
            typer.echo(
                "rebuild unavailable: embedding adapter and Chroma path are required",
                err=True,
            )
            raise typer.Exit(code=1)
        if resources.embedding is None or resources.chroma is None:
            typer.echo(
                "rebuild unavailable: embedding adapter and Chroma path are required",
                err=True,
            )
            raise typer.Exit(code=1)
        database = None
        try:
            database = Database(settings.database_path)
            revision = current_revision(f"sqlite:///{settings.database_path}")
            if revision != "0009_tool_call_provider_metadata":
                typer.echo("rebuild unavailable: schema is not at head", err=True)
                raise typer.Exit(code=1)
            summary = rebuild_index(
                IndexServices(database, SystemClock(), resources.embedding, resources.chroma)
            )
        except (RuntimeError, ValueError, TimeoutError) as error:
            typer.echo(safe_external_error(error), err=True)
            raise typer.Exit(code=1) from error
        finally:
            if database is not None:
                database.dispose()
            resources.close()
        typer.echo(
            f"documents={summary.total_documents} ready={summary.ready_documents} "
            f"failed={summary.failed_documents} chunks={summary.total_chunks} "
            f"upserted={summary.upserted_chunks} partial_failure={summary.partial_failure}"
        )
        if summary.partial_failure:
            raise typer.Exit(code=1)
