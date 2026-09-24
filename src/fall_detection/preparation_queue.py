"""Bounded, process-local coordination for synchronous preparation requests."""

from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from threading import BoundedSemaphore, Lock
from typing import TypeVar, cast

T = TypeVar("T")


class PreparationBusyError(RuntimeError):
    """Capacity or waiting time was exhausted."""

    pass


class PreparationCoordinator:
    """Share identical in-flight work and bound distinct preparations."""

    def __init__(self, slots: int = 2, wait_seconds: float = 10) -> None:
        """Set maximum active decodes and maximum time waiting for a slot."""
        if slots < 1 or wait_seconds < 0:
            raise ValueError("Preparation capacity must be positive")
        self._slots = BoundedSemaphore(slots)
        self._wait_seconds = wait_seconds
        self._lock = Lock()
        self._inflight: dict[tuple[object, ...], Future[object]] = {}

    def run(self, key: tuple[object, ...], prepare: Callable[[], T]) -> T:
        """Run or await one preparation, returning the shared result."""
        with self._lock:
            future = self._inflight.get(key)
            leader = future is None
            if leader:
                future = Future()
                self._inflight[key] = future
        assert future is not None
        if not leader:
            try:
                return cast("T", future.result(timeout=self._wait_seconds))
            except FutureTimeout as exc:
                raise PreparationBusyError(
                    "Preparation is still in progress; retry shortly"
                ) from exc
        acquired = False
        try:
            acquired = self._slots.acquire(timeout=self._wait_seconds)
            if not acquired:
                raise PreparationBusyError("Preparation capacity is full; retry shortly")
            result = prepare()
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            if acquired:
                self._slots.release()
            with self._lock:
                self._inflight.pop(key, None)
