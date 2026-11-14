"""Monotonic budgets across nested Kubernetes commands and observations."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import time

from fastapi import HTTPException

_deadline: ContextVar[float | None] = ContextVar("motor_operation_deadline", default=None)


def remaining_time(maximum: float) -> float:
    deadline = _deadline.get()
    remaining = maximum if deadline is None else min(maximum, deadline - time.monotonic())
    if remaining <= 0:
        raise HTTPException(504, "Kubernetes operation exceeded its total time budget; inspect the saved operation state")
    return remaining


def operation_budget(seconds: float):
    def decorate(function):
        @wraps(function)
        def bounded(*args, **kwargs):
            deadline = time.monotonic() + seconds
            inherited = _deadline.get()
            token = _deadline.set(min(deadline, inherited) if inherited is not None else deadline)
            try:
                remaining_time(seconds)
                return function(*args, **kwargs)
            finally:
                _deadline.reset(token)
        return bounded
    return decorate
