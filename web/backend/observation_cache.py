"""Small process-local read cache with non-queuing single-flight collection.

Cached observations keep their original timestamps. Invalidation is generation
based, so a collection started before a write cannot republish its old result.
"""
from __future__ import annotations

import copy
import threading
import time
from fastapi import HTTPException


class ObservationCache:
    def __init__(self, *, seconds: float = 10, failure_seconds: float = 3):
        self.seconds = seconds
        self.failure_seconds = failure_seconds
        self._guard = threading.Lock()
        self._generation = 0
        self._source = None
        self._running = False
        self._value = None
        self._until = 0.0
        self._error = None
        self._error_until = 0.0

    def invalidate(self):
        with self._guard:
            self._generation += 1
            self._value = self._error = None
            self._until = self._error_until = 0.0

    @staticmethod
    def _clone_error(error):
        if isinstance(error, HTTPException):
            return HTTPException(error.status_code, copy.deepcopy(error.detail), headers=copy.deepcopy(error.headers))
        try:
            return type(error)(str(error))
        except Exception:
            return RuntimeError("Resource observation is temporarily unavailable")

    def read(self, loader, *, source=None):
        with self._guard:
            if source != self._source:
                self._generation += 1
                self._source = source
                self._value = self._error = None
                self._until = self._error_until = 0.0
            current = time.monotonic()
            if self._value is not None and current < self._until:
                return copy.deepcopy(self._value)
            if self._running:
                if self._value is not None:
                    return copy.deepcopy(self._value)
                raise HTTPException(429, "Resource observation is already being collected; no duplicate query was queued",
                                    headers={"Retry-After": "2"})
            if self._error is not None and current < self._error_until:
                raise self._clone_error(self._error)
            generation = self._generation
            self._running = True
        try:
            value = loader()
        except BaseException as error:
            with self._guard:
                self._running = False
                if isinstance(error, Exception) and generation == self._generation:
                    self._error = self._clone_error(error)
                    self._error_until = time.monotonic() + self.failure_seconds
            raise
        with self._guard:
            self._running = False
            if generation != self._generation:
                raise HTTPException(429, "Resource state changed during collection; refresh the observation",
                                    headers={"Retry-After": "2"})
            self._value = copy.deepcopy(value)
            self._until = time.monotonic() + self.seconds
            self._error = None
            self._error_until = 0.0
            return copy.deepcopy(value)
