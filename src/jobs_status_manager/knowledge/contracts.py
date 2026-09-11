"""Typed Phase 5 knowledge boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from pydantic import BaseModel, ConfigDict, Field


@unique
class DocumentType(StrEnum):
    """Reusable knowledge categories."""

    JOB_REQUIREMENT = "JOB_REQUIREMENT"
    INTERVIEW_EXPERIENCE = "INTERVIEW_EXPERIENCE"
    INTERVIEW_KNOWLEDGE = "INTERVIEW_KNOWLEDGE"
    COMPANY_KNOWLEDGE = "COMPANY_KNOWLEDGE"


@unique
class KnowledgeLifecycle(StrEnum):
    """Relational document visibility."""

    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


@unique
class IndexStatus(StrEnum):
    """Rebuildable vector-index state."""

    PENDING = "PENDING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


@unique
class UserFileState(StrEnum):
    """Durable upload processing state."""

    PENDING = "PENDING"
    STORED = "STORED"
    PARSED = "PARSED"
    FAILED = "FAILED"


class ParsedDocument(BaseModel):
    """Validated text-only parsing result."""

    model_config = ConfigDict(frozen=True)

    source_file_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    document_type: DocumentType
    content: str = Field(min_length=1)
    company: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    position: str | None = Field(default=None, max_length=255)
    knowledge_domain: str | None = Field(default=None, max_length=255)
    tags: tuple[str, ...] = ()


class AddKnowledgeArguments(ParsedDocument):
    """Frozen AddKnowledge write arguments."""

    embedding_model: str = Field(default="fake-embedding", min_length=1, max_length=255)
    embedding_version: str = Field(default="v1", min_length=1, max_length=64)


class RemoveKnowledgeArguments(BaseModel):
    """Frozen RemoveKnowledge write arguments."""

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1, max_length=36)


class KnowledgeFilters(BaseModel):
    """Optional metadata filters for retrieval."""

    model_config = ConfigDict(frozen=True)

    document_type: DocumentType | None = None
    company: str | None = None
    department: str | None = None
    position: str | None = None
    knowledge_domain: str | None = None


class SearchKnowledgeInput(BaseModel):
    """Bounded retrieval request."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=2000)
    filters: KnowledgeFilters = Field(default_factory=KnowledgeFilters)
    top_k: int = Field(default=5, ge=1, le=20)
    max_documents: int = Field(default=5, ge=1, le=20)


class KnowledgeMetadata(BaseModel):
    """Filterable and explainable Chroma metadata."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    document_id: str
    document_type: str
    company: str = ""
    department: str = ""
    position: str = ""
    knowledge_domain: str = ""
    section_title: str = ""
    chunk_index: int
    embedding_version: str


class SearchKnowledgeResult(BaseModel):
    """One validated retrieval hit."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    document_title: str
    document_type: DocumentType
    content: str
    section_title: str | None = None
    score: float = Field(ge=0, le=1)
    metadata: KnowledgeMetadata


class SearchKnowledgeOutput(BaseModel):
    """Retrieval-only output consumed by the conversation LLM."""

    model_config = ConfigDict(frozen=True)

    results: tuple[SearchKnowledgeResult, ...]


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """One record written to the rebuildable vector index."""

    record_id: str
    content: str
    embedding: tuple[float, ...]
    metadata: KnowledgeMetadata


@dataclass(frozen=True, slots=True)
class VectorHit:
    """One raw vector-index query hit."""

    record_id: str
    distance: float
