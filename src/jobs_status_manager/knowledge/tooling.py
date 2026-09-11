"""Agent-facing knowledge tool boundaries."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from jobs_status_manager.agent.write_contracts import ToolExecution
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.knowledge.contracts import (
    AddKnowledgeArguments,
    DocumentType,
    KnowledgeFilters,
    SearchKnowledgeInput,
)
from jobs_status_manager.knowledge.files import parse_document
from jobs_status_manager.knowledge.index import IndexServices
from jobs_status_manager.knowledge.models import UserFile
from jobs_status_manager.knowledge.retrieval import search

type ToolArguments = dict[str, str | int | bool | None]


class SearchKnowledgeArguments(BaseModel):
    """Flat agent boundary for knowledge retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=2000)
    document_type: str | None = None
    company: str | None = None
    department: str | None = None
    position: str | None = None
    knowledge_domain: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    max_documents: int = Field(default=5, ge=1, le=20)


class AddKnowledgeToolArguments(BaseModel):
    """Flat agent boundary for confirmed knowledge creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_file_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    document_type: str
    content: str = Field(min_length=1)
    company: str | None = None
    department: str | None = None
    position: str | None = None
    knowledge_domain: str | None = None
    tags: str = ""
    embedding_model: str = "fake-embedding"
    embedding_version: str = "v1"

    def domain_arguments(self) -> AddKnowledgeArguments:
        """Parse flat tag text into frozen domain arguments."""
        values = tuple(tag.strip() for tag in self.tags.split(",") if tag.strip())
        return AddKnowledgeArguments.model_validate(
            {**self.model_dump(exclude={"tags"}), "tags": values}
        )


class ParseDocumentArguments(BaseModel):
    """Metadata supplied when parsing one persisted upload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    document_type: str
    company: str | None = None
    department: str | None = None
    position: str | None = None
    knowledge_domain: str | None = None
    tags: str = ""


def execute_knowledge(
    services: IndexServices,
    user_id: str,
    arguments: ToolArguments,
) -> ToolExecution:
    """Execute SearchKnowledge with injected external adapters."""
    flat = SearchKnowledgeArguments.model_validate(arguments)
    request = SearchKnowledgeInput(
        query=flat.query,
        filters=KnowledgeFilters(
            document_type=DocumentType(flat.document_type) if flat.document_type else None,
            company=flat.company,
            department=flat.department,
            position=flat.position,
            knowledge_domain=flat.knowledge_domain,
        ),
        top_k=flat.top_k,
        max_documents=flat.max_documents,
    )
    output = search(services, user_id, request)
    refs: dict[str, str | None] = {}
    if output.results:
        refs["knowledge_document_id"] = output.results[0].document_id
    return ToolExecution(output.model_dump_json(), refs)


def parse_document_tool(
    database: Database,
    user_id: str,
    arguments: ToolArguments,
) -> ToolExecution:
    """Parse one user-owned stored document outside its read session."""
    parsed = ParseDocumentArguments.model_validate(arguments)
    with Session(database.engine) as session:
        user_file = session.get(UserFile, parsed.file_id)
        if user_file is None or user_file.user_id != user_id or user_file.storage_path is None:
            raise LookupError(parsed.file_id)
        path = user_file.storage_path
    content = parse_document(Path(path))
    preview = AddKnowledgeToolArguments(
        source_file_id=parsed.file_id,
        title=parsed.title,
        document_type=parsed.document_type,
        content=content,
        company=parsed.company,
        department=parsed.department,
        position=parsed.position,
        knowledge_domain=parsed.knowledge_domain,
        tags=parsed.tags,
    )
    return ToolExecution(preview.model_dump_json(), {"file_id": parsed.file_id})
