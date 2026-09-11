"""Public knowledge management facade."""

from jobs_status_manager.knowledge.dependencies import KnowledgeActionResult, KnowledgeServices
from jobs_status_manager.knowledge.execution import execute_add, execute_remove
from jobs_status_manager.knowledge.index import retry_cleanup, retry_index
from jobs_status_manager.knowledge.proposals import propose_add, propose_remove
from jobs_status_manager.knowledge.rebuild import RebuildSummary, rebuild_index

__all__ = [
    "KnowledgeActionResult",
    "KnowledgeServices",
    "RebuildSummary",
    "execute_add",
    "execute_remove",
    "propose_add",
    "propose_remove",
    "rebuild_index",
    "retry_cleanup",
    "retry_index",
]
