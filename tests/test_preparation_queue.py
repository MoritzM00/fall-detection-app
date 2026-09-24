from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from fall_detection.preparation_queue import PreparationBusyError, PreparationCoordinator


def test_identical_requests_share_one_decode() -> None:
    coordinator = PreparationCoordinator(slots=1, wait_seconds=2)
    started = Event()
    release = Event()
    calls = 0

    def prepare() -> str:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return "bundle"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(coordinator.run, ("same",), prepare)
        assert started.wait(2)
        second = pool.submit(coordinator.run, ("same",), prepare)
        release.set()
        assert first.result() == second.result() == "bundle"
    assert calls == 1


def test_distinct_request_rejected_at_capacity() -> None:
    coordinator = PreparationCoordinator(slots=1, wait_seconds=0.01)
    started = Event()
    release = Event()

    def slow() -> str:
        started.set()
        assert release.wait(2)
        return "done"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(coordinator.run, ("one",), slow)
        assert started.wait(2)
        with pytest.raises(PreparationBusyError, match="capacity"):
            coordinator.run(("two",), lambda: "unexpected")
        release.set()
        assert first.result() == "done"
