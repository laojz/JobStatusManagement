"""Explicit transaction helper."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from jobs_status_manager.infrastructure.database.connection import Database


@contextmanager
def transaction(database: Database) -> Generator[Session, None, None]:
    """Yield a session and atomically commit or roll back its work."""
    with Session(database.engine, expire_on_commit=False) as session, session.begin():
        yield session
