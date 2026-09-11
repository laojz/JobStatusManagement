# Phase 5 Verification

**Acceptance result: PARTIAL PASS (2026-09-11).** The core relational and
vector workflow and all repository quality gates pass. The complete Phase 5
acceptance remains open for the E2E and rebuild scenarios listed below.

Phase 5 completes the local knowledge workflow:

```text
QQ file -> UserFile -> ParseDocument -> PendingAction
        -> KnowledgeDocument/KnowledgeChunk -> Embedding -> Chroma
        -> SearchKnowledge
```

## Delivered Behavior

- QQ accepts `.txt`, `.md`, `.markdown`, `.pdf`, and `.docx` files within the
  configured size limit. Validation, decoding, file writes, parsing, Embedding,
  and Chroma operations run outside database transactions.
- `ParseDocument` returns text and frozen `AddKnowledge` arguments without
  changing knowledge facts.
- `AddKnowledge` and `RemoveKnowledge` require the existing PendingAction
  confirmation flow. Add atomically creates the relational document, stable
  chunks, and `KNOWLEDGE_DOCUMENT_ADDED`; remove first makes the document
  ineligible for retrieval and then permits asynchronous Chroma cleanup.
- SQLite stores the authoritative document lifecycle and indexing state.
  Chroma uses `KnowledgeChunk.id` as its record ID and can be rebuilt from the
  relational data.
- The lifecycle worker advances pending documents through
  `PENDING -> INDEXING -> READY`. Embedding or Chroma failures retain the
  document and chunks and set `FAILED` with the error for retry.
- `SearchKnowledge` first selects only the requesting user's `ACTIVE` and
  `READY` documents, then validates vector hits against relational facts.

The production lifecycle accepts typed Embedding and Chroma adapters. This
phase includes deterministic fakes and a local Chroma adapter; it does not
implement or smoke-test a real external Embedding provider.

## Automated Verification

The following checks passed on 2026-09-11:

| Command | Result |
| --- | --- |
| `uv run pytest -q` | `58 passed` |
| `uv run ruff check .` | pass |
| `uv run ruff format --check .` | pass |
| `uv run basedpyright` | `0 errors, 0 warnings, 0 notes` |
| Python source-policy audit | no violations in 31 touched files |
| LocalChroma upsert/query/delete smoke test | pass |

Coverage includes upload transaction boundaries and orphan cleanup,
confirmation-gated relational writes, stable chunk indexing, retrieval,
immediate relational removal, Chroma cleanup, and persisted Embedding failure
state.

## Migration Verification

Migration `0006_phase5_rag` adds `user_files`, `knowledge_documents`,
`knowledge_chunks`, and the session's active knowledge reference. A fresh
temporary SQLite database completed this round trip:

```text
upgrade   0006_phase5_rag
downgrade 0005_conversation_agent
reupgrade 0006_phase5_rag
```

No real IMAP, QQ, LLM, Embedding, or hosted vector credentials are required by
the verification suite.

## Verified Acceptance Scope

- File bytes are written before relational transactions, and failed metadata
  persistence removes the newly stored file.
- Confirmed AddKnowledge creates one ACTIVE document and stable chunks with
  initial `PENDING` index state; relational facts survive Embedding failure.
- The lifecycle index cycle writes chunk IDs to Chroma, reaches `READY`, and
  SearchKnowledge returns only relationally eligible results.
- Confirmed removal immediately excludes the document from retrieval and the
  cleanup cycle deletes its Chroma records.
- Migration `0006_phase5_rag`, health reporting, Tool registration, and the
  existing Phase 0-4 behavior pass the complete 58-test suite.

## Remaining Acceptance Gaps

- The full QQ PDF upload -> ParseDocument -> AddKnowledge confirmation -> index
  -> search scenario is not yet covered through the HTTP/Agent surface.
- The Application + Mail + SearchKnowledge three-tool sequence is not yet
  covered by one composite AgentRun E2E test.
- Stale `INDEXING` restart recovery and a complete Chroma-loss rebuild are not
  exercised. A rebuild command is deferred to the Phase 6 reliability scope.
- Alibaba Cloud Bailian Embedding is implemented and wired for production
  composition; no real provider request was made during verification.
