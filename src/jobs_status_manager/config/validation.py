"""Configuration error rendering."""

from pydantic import ValidationError


def safe_settings_error(error: ValidationError) -> str:
    """Render only configuration field names and safe validation messages."""
    fields = (".".join(str(part) for part in item["loc"]) for item in error.errors())
    return "invalid configuration fields: " + ", ".join(fields)
