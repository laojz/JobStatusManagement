# REQ-F03 ReplyTarget Evidence

**Date:** 2026-09-13  
**Status:** Local deterministic contract verified

## Scope

This record covers provider-neutral passive/proactive target validation,
reconstruction, and fake gateway routing. It is not live QQ send/receive or
provider compatibility evidence.

| Field | Record |
|---|---|
| Commit/artifact | Current worktree based on `62c5ad427a0796c88709cc15dbaae52e6d1e0216`; changes are uncommitted |
| Environment | Temporary SQLite, fake gateway, local adapter tests, no QQ network |
| Command | `uv run pytest -q tests/unit/test_qq_reply_contracts.py tests/unit/test_qq_adapter.py tests/integration/test_qq_reply_target_persistence.py tests/integration/test_conversation_agent.py` |
| Exit code | `0` |
| Result | `135 passed` |
| Schema head | `0008_qq_reply_targets (head)`; no new migration added |
| Cleanup | Temporary databases and fake gateway resources closed by tests |
| Limitations | Proves local target contracts and restart reconstruction only; no live QQ exchange is asserted |

## Covered assertions

- Complete passive targets preserve provider, scope, target, message ID, event
  ID, and optional sequence.
- Missing passive fields fail closed instead of becoming proactive sends.
- Proactive targets reject passive-only metadata.
- `reply`, `deliver`, and `push` use distinct local routing semantics.
- Replaying persisted local events does not change target mode or create
  duplicate business facts.
