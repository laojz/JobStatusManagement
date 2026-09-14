"""Application health endpoint."""

from typing import Final, TypedDict

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from jobs_status_manager.application.operations import list_tasks
from jobs_status_manager.infrastructure.database.migrations import current_revision

HEAD_REVISION: Final = "0009_tool_call_provider_metadata"


class HealthPayload(TypedDict):
    """JSON shape returned by the health endpoint."""

    status: str
    phase: str
    database: str
    schema_version: str | None
    readiness: str
    durable_tasks: str
    failed_tasks: int
    stale_tasks: int
    runtime_mode: str
    imap: str
    llm: str
    product_readiness: str


def live(_: Request) -> Response:
    """Return process liveness without consulting application dependencies."""
    return JSONResponse({"status": "alive"}, status_code=200)


def health(request: Request) -> Response:
    """Return readiness and schema status without business data."""
    database = request.state.database
    readiness = request.state.readiness
    database_status = "ok"
    schema_version: str | None = None
    failed_tasks = 0
    stale_tasks = 0
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        schema_version = current_revision(f"sqlite:///{database.path}")
        if schema_version is None or schema_version != HEAD_REVISION:
            database_status = "not_ready"
        if database_status == "ok":
            tasks = list_tasks(database, include_stale=True)
            failed_tasks = sum(not task.stale for task in tasks)
            stale_tasks = sum(task.stale for task in tasks)
    except (OSError, RuntimeError, SQLAlchemyError):
        database_status = "unavailable"

    external_ready = readiness["product_readiness"] in {"ready", "local_only"}
    ready = database_status == "ok" and schema_version == HEAD_REVISION and external_ready
    durable_tasks = "degraded" if failed_tasks or stale_tasks else "ok"
    payload: HealthPayload = {
        "status": "ready" if ready else "not_ready",
        "phase": "6",
        "database": database_status,
        "schema_version": schema_version,
        "readiness": "ready" if ready else "not_ready",
        "durable_tasks": durable_tasks,
        "failed_tasks": failed_tasks,
        "stale_tasks": stale_tasks,
        "runtime_mode": readiness["runtime_mode"],
        "imap": readiness["imap"],
        "llm": readiness["llm"],
        "product_readiness": readiness["product_readiness"] if ready else "not_ready",
    }
    return JSONResponse(payload, status_code=200 if ready else 503)
