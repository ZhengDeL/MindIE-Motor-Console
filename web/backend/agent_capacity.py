"""Process-wide, non-queuing limits for expensive native Motor generation.

The agent runs one worker. Nested calls on the same thread reuse their slot;
unrelated requests receive backpressure before waiting on a service lock.
This never changes limits on Motor workload containers or Kubernetes Pods.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import os
import threading

from fastapi import HTTPException


class NativeCapacity:
    def __init__(self, limit: int = 1, *, retry_after: int = 5):
        if type(limit) is not int or not 1 <= limit <= 4:
            raise ValueError("MOTOR_AGENT_NATIVE_CONCURRENCY must be an integer between 1 and 4")
        self.limit = limit
        self.retry_after = retry_after
        self._slots = threading.BoundedSemaphore(limit)
        self._local = threading.local()

    @contextmanager
    def slot(self):
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        if not self._slots.acquire(blocking=False):
            raise HTTPException(
                429,
                "Agent native generation capacity is busy; retry after "
                + str(self.retry_after) + " seconds. No operation was queued.",
                headers={"Retry-After": str(self.retry_after)},
            )
        self._local.depth = 1
        try:
            yield
        finally:
            self._local.depth = 0
            self._slots.release()


_capacity: NativeCapacity | None = None
_capacity_lock = threading.Lock()


def native_capacity() -> NativeCapacity:
    """Read process configuration once; invalid configuration fails closed."""
    global _capacity
    with _capacity_lock:
        if _capacity is None:
            raw = os.getenv("MOTOR_AGENT_NATIVE_CONCURRENCY", "1")
            try:
                if not raw.isascii() or not raw.isdecimal():
                    raise ValueError
                _capacity = NativeCapacity(int(raw))
            except ValueError as exc:
                raise HTTPException(503, "MOTOR_AGENT_NATIVE_CONCURRENCY must be an integer between 1 and 4") from exc
        return _capacity


def native_operation(function):
    """Acquire before service locks; release after every return or exception."""
    @wraps(function)
    def limited(*args, **kwargs):
        with native_capacity().slot():
            return function(*args, **kwargs)
    return limited


@contextmanager
def native_service_lock(lock):
    """Heavy operations do not queue behind another operation on one service."""
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "This service has an Agent operation in progress; retry after 5 seconds. No operation was queued.",
                            headers={"Retry-After": "5"})
    try:
        yield
    finally:
        lock.release()
