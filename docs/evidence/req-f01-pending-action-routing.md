# REQ-F01 PendingAction Routing Evidence

**Date:** 2026-09-13  
**Status:** Local implementation verified

## Scope

This record covers local SQLite, outbox, notification, and `ProcessedEvent`
behavior only. It does not validate QQ delivery or any live provider.

| Field | Record |
|---|---|
| Commit/artifact | Current worktree based on `62c5ad427a0796c88709cc15dbaae52e6d1e0216`; changes are uncommitted |
| Environment | Temporary file-backed SQLite, fake LLM, no live provider |
| Command | `uv run pytest -q tests/integration/test_knowledge_event_routing.py tests/integration/test_mail_pipeline.py` |
| Exit code | `0` |
| Result | `40 passed` |
| Schema head | Existing `0008_qq_reply_targets`; no migration added |
| Cleanup | Pytest temporary database fixtures disposed; no repository data created |
| Limitations | Proves local event routing and persistence only; notification delivery through QQ remains excluded |

## Covered assertions

- `AddKnowledge` and `RemoveKnowledge` create knowledge confirmation
  notifications without a mail-analysis association.
- `UpdateApplicationStatus` retains the mail-analysis-to-mail association.
- Duplicate event consumption creates one notification and one
  `ProcessedEvent`.
- Unsupported action types and mismatched sources leave the event pending and
  record a diagnostic failure without a partial notification.
