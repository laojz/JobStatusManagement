"""Configuration error rendering."""

from pydantic import ValidationError


def safe_settings_error(error: ValidationError) -> str:
    """Render only configuration field names and safe validation messages."""
    errors = error.errors()
    fields = (".".join(str(part) for part in item["loc"]) or "configuration" for item in errors)
    return "invalid configuration fields: " + ", ".join(fields)
