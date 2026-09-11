"""Identifier generation abstractions."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4


class IdGenerator(Protocol):
    """Identifier generation contract."""

    def new_id(self) -> UUID:
        """Return a new identifier."""
        ...


class UUIDGenerator:
    """Random UUID generator used in runtime."""

    def new_id(self) -> UUID:
        """Return a new UUID4."""
        return uuid4()


@dataclass(slots=True)
class DeterministicIdGenerator:
    """Sequential UUID generator for deterministic tests."""

    values: list[UUID]
    _index: int = field(default=0, init=False)

    def new_id(self) -> UUID:
        """Return the next configured UUID."""
        if self._index >= len(self.values):
            message = "deterministic ID sequence exhausted"
            raise RuntimeError(message)
        value = self.values[self._index]
        self._index += 1
        return value
