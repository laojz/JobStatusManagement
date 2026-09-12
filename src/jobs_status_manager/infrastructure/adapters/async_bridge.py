"""AnyIO bridge for synchronous provider gateway operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import anyio

if TYPE_CHECKING:
    from jobs_status_manager.agent.contracts import ReplyTarget
    from jobs_status_manager.infrastructure.adapters.protocols import QQDeliveryResult


class _SyncQQOperations(Protocol):
    """Outbound synchronous operations exposed by a QQ gateway."""

    def push(self, user_id: str, content: str) -> QQDeliveryResult: ...

    def reply(self, user_id: str, message_id: str, content: str) -> QQDeliveryResult: ...

    def deliver(self, target: ReplyTarget, content: str) -> QQDeliveryResult: ...

    def send_image(
        self, target: ReplyTarget, content_type: str, content: bytes
    ) -> QQDeliveryResult: ...

    def send_file(
        self, target: ReplyTarget, filename: str, content_type: str, content: bytes
    ) -> QQDeliveryResult: ...


class AsyncQQGatewayBridge:
    """Run blocking gateway calls in AnyIO worker threads without abandoning them."""

    def __init__(self, gateway: _SyncQQOperations) -> None:
        """Store the synchronous gateway used by worker-thread calls."""
        self._gateway = gateway

    async def push(self, user_id: str, content: str) -> QQDeliveryResult:
        """Push content without blocking the event loop."""
        return await anyio.to_thread.run_sync(
            self._gateway.push, user_id, content, abandon_on_cancel=False
        )

    async def reply(self, user_id: str, message_id: str, content: str) -> QQDeliveryResult:
        """Reply without blocking the event loop."""
        return await anyio.to_thread.run_sync(
            self._gateway.reply, user_id, message_id, content, abandon_on_cancel=False
        )

    async def deliver(self, target: ReplyTarget, content: str) -> QQDeliveryResult:
        """Deliver through the explicit passive or proactive target."""
        return await anyio.to_thread.run_sync(
            self._gateway.deliver, target, content, abandon_on_cancel=False
        )

    async def send_image(
        self, target: ReplyTarget, content_type: str, content: bytes
    ) -> QQDeliveryResult:
        """Send an image through the typed gateway seam."""
        return await anyio.to_thread.run_sync(
            self._gateway.send_image, target, content_type, content, abandon_on_cancel=False
        )

    async def send_file(
        self, target: ReplyTarget, filename: str, content_type: str, content: bytes
    ) -> QQDeliveryResult:
        """Send a file through the typed gateway seam."""
        return await anyio.to_thread.run_sync(
            self._gateway.send_file,
            target,
            filename,
            content_type,
            content,
            abandon_on_cancel=False,
        )
