"""Bounded async network work behind the control plane's synchronous API.

HTTP read timeouts limit inactivity, not total elapsed time. One process-local
I/O loop applies cancellation to the whole operation, including response headers
and individual body reads. It has a fixed DNS executor and request capacity;
timed-out operations are cancelled rather than left in an unbounded thread pool.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
_guard = threading.Lock()
_runner = None


class _NetworkLoop:
    def __init__(self):
        self.pid = os.getpid()
        self.loop = asyncio.new_event_loop()
        self.loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="qinghuan-agent-helper"))
        self.capacity = None
        self.thread = threading.Thread(target=self._serve, name="qinghuan-agent-network", daemon=True)
        self.thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        self.capacity = asyncio.Semaphore(16)
        self.loop.run_forever()

    async def _bounded(self, factory: Callable[[], Awaitable[T]], deadline: float) -> T:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Agent request deadline expired before execution")
        async with asyncio.timeout(remaining):
            async with self.capacity:
                return await factory()

    def run(self, factory: Callable[[], Awaitable[T]], deadline: float) -> T:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Agent request deadline expired before execution")
        future = asyncio.run_coroutine_threadsafe(self._bounded(factory, deadline), self.loop)
        try:
            # Caller returns at its absolute deadline. Async cancellation closes
            # the socket on the I/O loop; it never replays a timed-out write.
            return future.result(timeout=remaining)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("Agent request exceeded its total time budget") from None


def run_network(factory: Callable[[], Awaitable[T]], deadline: float) -> T:
    global _runner
    with _guard:
        if _runner is None or _runner.pid != os.getpid():
            _runner = _NetworkLoop()
        runner = _runner
    return runner.run(factory, deadline)
