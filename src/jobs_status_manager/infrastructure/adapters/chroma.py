"""Local single-collection Chroma adapter."""

from contextlib import suppress
from pathlib import Path
from typing import Final

import chromadb
from chromadb.errors import NotFoundError

from jobs_status_manager.knowledge.contracts import VectorHit, VectorRecord

COLLECTION_NAME: Final = "job_knowledge_v1"


class LocalChroma:
    """Persistent Chroma index with caller-supplied embeddings."""

    def __init__(self, path: Path) -> None:
        """Open the single local collection."""
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, records: tuple[VectorRecord, ...]) -> None:
        """Upsert records using KnowledgeChunk IDs."""
        if not records:
            return
        self._collection.upsert(
            ids=[record.record_id for record in records],
            embeddings=[list(record.embedding) for record in records],
            documents=[record.content for record in records],
            metadatas=[record.metadata.model_dump() for record in records],
        )

    def query(
        self,
        embedding: tuple[float, ...],
        document_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[VectorHit, ...]:
        """Query only relationally eligible document IDs."""
        if not document_ids:
            return ()
        result = self._collection.query(
            query_embeddings=[list(embedding)],
            n_results=limit,
            where={"document_id": {"$in": list(document_ids)}},
            include=["distances"],
        )
        ids = result["ids"][0]
        distances = result["distances"]
        if distances is None:
            return ()
        return tuple(
            VectorHit(record_id, float(distance))
            for record_id, distance in zip(ids, distances[0], strict=True)
        )

    def delete(self, record_ids: tuple[str, ...]) -> None:
        """Delete stable IDs; missing records are harmless."""
        if record_ids:
            self._collection.delete(ids=list(record_ids))

    def reset(self) -> None:
        """Recreate the collection so stale records cannot survive a rebuild."""
        with suppress(NotFoundError):
            self._client.delete_collection(COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
