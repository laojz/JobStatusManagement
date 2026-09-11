"""Typed errors for the Phase 1 application core."""


class ApplicationCoreError(Exception):
    """Base class for expected application-core failures."""


class InvalidPendingActionTransitionError(ApplicationCoreError):
    """Raised when an action state transition is not allowed."""

    def __init__(self, current_state: str, target_state: str) -> None:
        """Capture the current and requested states."""
        self.current_state = current_state
        self.target_state = target_state
        super().__init__()

    def __str__(self) -> str:
        """Render a safe transition error."""
        return f"invalid PendingAction transition: {self.current_state} -> {self.target_state}"


class PendingActionNotFoundError(ApplicationCoreError):
    """Raised when an action is absent or belongs to another user."""

    def __init__(self, confirmation_code: str) -> None:
        """Capture the user-supplied action code."""
        self.confirmation_code = confirmation_code
        super().__init__()

    def __str__(self) -> str:
        """Render a safe lookup error."""
        return f"pending action not found: {self.confirmation_code}"


class AmbiguousPendingActionError(ApplicationCoreError):
    """Raised when a code-less command has multiple targets."""

    def __init__(self, count: int) -> None:
        """Capture the number of possible targets."""
        self.count = count
        super().__init__()

    def __str__(self) -> str:
        """Render the ambiguity count."""
        return f"confirmation is ambiguous across {self.count} pending actions"


class ResolvedArgumentsTamperedError(ApplicationCoreError):
    """Raised when persisted action arguments cannot be parsed."""

    def __init__(self, action_id: str) -> None:
        """Capture the corrupted action identifier."""
        self.action_id = action_id
        super().__init__()

    def __str__(self) -> str:
        """Render a safe argument-integrity error."""
        return f"resolved arguments are invalid for action {self.action_id}"
