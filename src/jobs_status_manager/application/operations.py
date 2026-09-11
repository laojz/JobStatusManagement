"""Stable public import surface for Phase 6 operations."""

from jobs_status_manager.application.database_operations import (
    backup_database,
    check_integrity,
    restore_check,
)
from jobs_status_manager.application.operation_contracts import (
    BackupResult,
    DatabaseMaintenanceError,
    IntegrityResult,
    OperationResult,
    OperationsError,
    TaskKind,
    TaskRetryError,
    TaskSummary,
)
from jobs_status_manager.application.task_operations import list_tasks, retry_task

__all__ = [
    "BackupResult",
    "DatabaseMaintenanceError",
    "IntegrityResult",
    "OperationResult",
    "OperationsError",
    "TaskKind",
    "TaskRetryError",
    "TaskSummary",
    "backup_database",
    "check_integrity",
    "list_tasks",
    "restore_check",
    "retry_task",
]
