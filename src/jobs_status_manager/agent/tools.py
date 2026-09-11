"""Application, Mail, and RAG tool registry."""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from jobs_status_manager.agent.contracts import ToolDefinition, ToolPermission
from jobs_status_manager.agent.query_tools import (
    ApplicationToolName,
    execute_application,
    execute_mail,
)
from jobs_status_manager.agent.write_contracts import (
    ToolExecution,
    UpdateApplicationStatusArguments,
)
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.knowledge.contracts import RemoveKnowledgeArguments
from jobs_status_manager.knowledge.tooling import (
    AddKnowledgeToolArguments,
    ParseDocumentArguments,
    SearchKnowledgeArguments,
    parse_document_tool,
)

TOOL_NAMES: Final = (
    "SearchApplications",
    "GetApplication",
    "GetApplicationStatus",
    "GetStatusHistory",
    "SearchMails",
    "GetRecentMails",
    "GetMailAnalysis",
    "GetMailContent",
    "SearchKnowledge",
    "ParseDocument",
)
WRITE_TOOL_NAME: Final = "UpdateApplicationStatus"
ADD_KNOWLEDGE_TOOL: Final = "AddKnowledge"
REMOVE_KNOWLEDGE_TOOL: Final = "RemoveKnowledge"
WRITE_TOOL_NAMES: Final = (WRITE_TOOL_NAME, ADD_KNOWLEDGE_TOOL, REMOVE_KNOWLEDGE_TOOL)
TOOL_INPUT_SCHEMAS: Final = {
    "SearchApplications": {"company": "string?"},
    "GetApplication": {"application_id": "string"},
    "GetApplicationStatus": {"application_id": "string"},
    "GetStatusHistory": {"application_id": "string"},
    "SearchMails": {"query": "string?"},
    "GetRecentMails": {},
    "GetMailAnalysis": {"mail_id": "string"},
    "GetMailContent": {"mail_id": "string"},
    "SearchKnowledge": {
        "query": "string",
        "document_type": "DocumentType?",
        "company": "string?",
        "department": "string?",
        "position": "string?",
        "knowledge_domain": "string?",
        "top_k": "integer?",
        "max_documents": "integer?",
    },
    "ParseDocument": {
        "file_id": "string",
        "title": "string",
        "document_type": "DocumentType",
        "company": "string?",
        "department": "string?",
        "position": "string?",
        "knowledge_domain": "string?",
        "tags": "comma-separated string?",
    },
}
type ToolArguments = dict[str, str | int | bool | None]
type WriteToolName = Literal["UpdateApplicationStatus", "AddKnowledge", "RemoveKnowledge"]


class EmptyArguments(BaseModel):
    """Arguments for a tool that accepts no fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanyArguments(BaseModel):
    """Optional company filter for application search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company: str | None = Field(default=None, max_length=255)


class QueryArguments(BaseModel):
    """Optional text filter for mail search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str | None = Field(default=None, max_length=255)


class IdentifierArguments(BaseModel):
    """Application identifier for application detail tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: str = Field(min_length=1, max_length=36)


class MailIdentifierArguments(BaseModel):
    """Mail identifier for mail detail tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mail_id: str = Field(min_length=1, max_length=36)


TOOL_ARGUMENT_MODELS: Final = {
    "SearchApplications": CompanyArguments,
    "GetApplication": IdentifierArguments,
    "GetApplicationStatus": IdentifierArguments,
    "GetStatusHistory": IdentifierArguments,
    "SearchMails": QueryArguments,
    "GetRecentMails": EmptyArguments,
    "GetMailAnalysis": MailIdentifierArguments,
    "GetMailContent": MailIdentifierArguments,
    "SearchKnowledge": SearchKnowledgeArguments,
    "ParseDocument": ParseDocumentArguments,
    WRITE_TOOL_NAME: UpdateApplicationStatusArguments,
    ADD_KNOWLEDGE_TOOL: AddKnowledgeToolArguments,
    REMOVE_KNOWLEDGE_TOOL: RemoveKnowledgeArguments,
}


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the Phase 4 registry with one confirmation-gated write."""
    return (
        *(
            ToolDefinition(
                name=name,
                description=f"Read-only {name}",
                category="QUERY",
                permission=ToolPermission.READ,
                input_schema=TOOL_INPUT_SCHEMAS[name],
                output_schema={"data": "bounded JSON string", "context_refs": "entity IDs"},
            )
            for name in TOOL_NAMES
        ),
        *(
            ToolDefinition(
                name=name,
                description=f"Propose {name}; confirmation is required",
                category="COMMAND",
                permission=ToolPermission.WRITE,
                requires_confirmation=True,
                input_schema={
                    key: str(value)
                    for key, value in model.model_json_schema()["properties"].items()
                },
                output_schema={
                    "pending_action_id": "string",
                    "confirmation_code": "string",
                    "display_summary": "string",
                },
            )
            for name, model in (
                (WRITE_TOOL_NAME, UpdateApplicationStatusArguments),
                (ADD_KNOWLEDGE_TOOL, AddKnowledgeToolArguments),
                (REMOVE_KNOWLEDGE_TOOL, RemoveKnowledgeArguments),
            )
        ),
    )


def execute(
    database: Database,
    user_id: str,
    name: str,
    arguments: ToolArguments,
) -> ToolExecution:
    """Execute one registered read-only query in an isolated read session."""
    if name not in (*TOOL_NAMES, *WRITE_TOOL_NAMES):
        message = f"tool is not registered: {name}"
        raise PermissionError(message)
    model = TOOL_ARGUMENT_MODELS[name]
    try:
        parsed = model.model_validate(arguments)
    except ValidationError as exception:
        message = f"malformed arguments for {name}"
        raise ValueError(message) from exception
    normalized = TypeAdapter(ToolArguments).validate_python(parsed.model_dump(exclude_none=True))
    if name in WRITE_TOOL_NAMES:
        message = "write tool requires an agent proposal context"
        raise PermissionError(message)
    if name == "SearchKnowledge":
        message = "knowledge adapters are unavailable"
        raise RuntimeError(message)
    if name == "ParseDocument":
        return parse_document_tool(database, user_id, normalized)
    with Session(database.engine, expire_on_commit=False) as session:
        if name in TOOL_NAMES[:4]:
            application_name = TypeAdapter(ApplicationToolName).validate_python(name)
            return execute_application(session, user_id, application_name, normalized)
        return execute_mail(session, user_id, name, normalized)
