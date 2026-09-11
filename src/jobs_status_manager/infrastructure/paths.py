"""Application filesystem path handling."""

import os
from contextlib import suppress
from pathlib import Path


def ensure_private_directory(path: Path) -> Path:
    """Create a directory and request owner-only permissions where supported."""
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(0o700)
    return path


def prepare_data_paths(data_dir: Path, database_path: Path) -> None:
    """Create the data and database parent directories."""
    _ = ensure_private_directory(data_dir)
    _ = ensure_private_directory(database_path.parent)
    if os.name != "nt":
        database_path.parent.chmod(0o700)
