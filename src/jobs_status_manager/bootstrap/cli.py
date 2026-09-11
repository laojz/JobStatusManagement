"""Stable Typer facade for all CLI commands."""

import typer

from jobs_status_manager.bootstrap.cli_demos import register as register_demos
from jobs_status_manager.bootstrap.cli_operations import register as register_operations
from jobs_status_manager.bootstrap.cli_runtime import register as register_runtime

app = typer.Typer(add_completion=False, no_args_is_help=True)

register_operations(app)
register_runtime(app)
register_demos(app)


def main() -> None:
    """Run the CLI."""
    app()
