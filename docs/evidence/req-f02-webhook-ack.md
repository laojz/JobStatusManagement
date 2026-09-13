# REQ-F02 Webhook ACK Evidence

**Date:** 2026-09-13  
**Status:** Local deterministic contract verified

## Scope

This is local deterministic QQ transport evidence using fake handlers and
transport seams. It is not live QQ webhook validation and not provider
compatibility evidence.

| Field | Record |
|---|---|
| Commit/artifact | Current worktree based on `62c5ad427a0796c88709cc15dbaae52e6d1e0216`; changes are uncommitted |
| Environment | Local Starlette transport, fake handler, temporary test state, no QQ network |
| Command | `uv run pytest -q tests/unit/test_qq_adapter.py tests/unit/test_lifecycle.py tests/integration/test_qq_webhook.py` |
| Exit code | `0` |
| Result | `135 passed` |
| Schema head | Existing `0008_qq_reply_targets`; no migration added |
| Cleanup | AnyIO task groups and transport fixtures closed by tests |
| Limitations | Does not prove live QQ ACK behavior, provider redelivery behavior, or online restart recovery |

## Covered assertions

- Durable receipt completion precedes a successful ordinary ACK.
- A successful ACK does not wait for a blocked post-receipt handler when the
  lifecycle-owned task group is bound.
- Transport close cancels pending post-receipt handlers.
- Persistence errors return non-success responses.
- Handler errors after receipt do not revoke the accepted ACK.
