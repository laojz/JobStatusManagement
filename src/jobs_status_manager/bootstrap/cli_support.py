"""Shared configuration helpers for the command line interface."""

from pathlib import Path

import typer
from pydantic import ValidationError

from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.config.validation import safe_settings_error


def load_settings() -> AppSettings:
    """Load settings and render validation errors without values."""
    try:
        return AppSettings.from_environment()
    except ValidationError as error:
        typer.echo(safe_settings_error(error), err=True)
        raise typer.Exit(code=2) from error


def project_root() -> Path:
    """Return the repository root from the installed source layout."""
    return Path(__file__).resolve().parents[3]
