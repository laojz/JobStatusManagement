from threading import Event
from time import monotonic

import anyio
import pytest

from jobs_status_manager.agent.contracts import ReplyMode, ReplyTarget
from jobs_status_manager.infrastructure.adapters.async_bridge import AsyncQQGatewayBridge
from jobs_status_manager.infrastructure.adapters.fakes import FakeQQDeliveryResult, FakeQQGateway


class BlockingGateway:
    def __init__(self, started: Event, release: Event) -> None:
        self.started = started
        self.release = release
        self.gateway = FakeQQGateway()

    def push(self, user_id: str, content: str) -> FakeQQDeliveryResult:
        return self.gateway.push(user_id, content)

    def reply(self, user_id: str, message_id: str, content: str) -> FakeQQDeliveryResult:
        return self.gateway.reply(user_id, message_id, content)

    def deliver(self, target: ReplyTarget, content: str) -> FakeQQDeliveryResult:
        self.started.set()
        self.release.wait(timeout=2)
        return self.gateway.deliver(target, content)

    def send_image(
        self, target: ReplyTarget, content_type: str, content: bytes
    ) -> FakeQQDeliveryResult:
        return self.gateway.send_image(target, content_type, content)

    def send_file(
        self, target: ReplyTarget, filename: str, content_type: str, content: bytes
    ) -> FakeQQDeliveryResult:
        return self.gateway.send_file(target, filename, content_type, content)


@pytest.mark.anyio
async def test_bridge_moves_sync_delivery_off_event_loop() -> None:
    started = Event()
    release = Event()
    gateway = BlockingGateway(started, release)
    bridge = AsyncQQGatewayBridge(gateway)
    target = ReplyTarget(ReplyMode.PROACTIVE, "qq", "c2c", "openid")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bridge.deliver, target, "hello")
        with anyio.fail_after(1):
            await anyio.to_thread.run_sync(started.wait)
        began = monotonic()
        await anyio.lowlevel.checkpoint()
        assert monotonic() - began < 0.2
        release.set()
        tasks.cancel_scope.cancel()


@pytest.mark.anyio
async def test_bridge_cancellation_waits_for_sync_call_to_finish() -> None:
    started = Event()
    release = Event()
    gateway = BlockingGateway(started, release)
    bridge = AsyncQQGatewayBridge(gateway)
    target = ReplyTarget(ReplyMode.PROACTIVE, "qq", "c2c", "openid")
    completed = Event()

    async def call() -> None:
        await bridge.deliver(target, "hello")
        completed.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(call)
        with anyio.fail_after(1):
            await anyio.to_thread.run_sync(started.wait)
        tasks.cancel_scope.cancel()
        release.set()
    assert completed.is_set()


@pytest.mark.anyio
async def test_bridge_routes_image_and_file_to_typed_operations() -> None:
    gateway = BlockingGateway(Event(), Event())
    bridge = AsyncQQGatewayBridge(gateway)
    target = ReplyTarget(ReplyMode.PROACTIVE, "qq", "c2c", "openid")

    await bridge.send_image(target, "image/png", b"png")
    await bridge.send_file(target, "report.txt", "text/plain", b"report")

    assert gateway.gateway.calls == [
        ("send_image", ("qq", "c2c", "openid", "image/png", "3")),
        ("send_file", ("qq", "c2c", "openid", "report.txt", "text/plain", "6")),
    ]
