"""GPU serving status that never wakes a scale-to-zero endpoint, plus explicit warm-up."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict

import httpx

from fall_detection.config import Settings

# Control-plane counts can lag a fresh container; trust a warm-up this long before them.
READY_GRACE_SECONDS = 60

WarmupState = Literal["idle", "warming", "ready", "failed"]
ServingState = Literal["simulated", "unmonitored", "unknown", "idle", "running", "warming", "ready"]


class WarmupView(TypedDict):
    """Progress of the most recent explicit warm-up."""

    state: WarmupState
    seconds: float | None
    error: str | None


class StatusSnapshot(TypedDict):
    """Serving state as reported to the web UI."""

    state: ServingState
    containers: int | None
    backlog: int | None
    detail: str | None
    warmup: WarmupView


@dataclass(frozen=True, slots=True)
class ContainerStats:
    """Control-plane view of the serving function, read without contacting vLLM."""

    containers: int
    backlog: int


def modal_container_stats(app_name: str, function_name: str = "serve") -> ContainerStats:
    """Ask Modal how many containers serve the function; this does not start one."""
    import modal

    stats = modal.Function.from_name(app_name, function_name).get_current_stats()
    return ContainerStats(containers=stats.num_total_runners, backlog=stats.backlog)


def wait_until_healthy(
    settings: Settings,
    deadline_seconds: float = 900,
    poll_seconds: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """Poll the vLLM health route until it answers 200; return the elapsed seconds.

    Each poll is a real request, so this wakes a scale-to-zero endpoint on purpose.
    """
    if settings.backend_kind != "vllm":
        raise ValueError("Set FALL_DETECTION_BACKEND_KIND=vllm to warm a GPU endpoint")
    health_url = f"{settings.inference_base_url.removesuffix('/v1')}/health"
    headers = settings.inference_auth_headers()
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if elapsed > deadline_seconds:
            raise TimeoutError(f"endpoint not healthy after {deadline_seconds:.0f} s")
        try:
            # Modal holds the request during a cold start and redirects past ~150 s.
            response = httpx.get(
                health_url,
                headers=headers,
                timeout=deadline_seconds - elapsed,
                follow_redirects=True,
            )
            if response.status_code == 200:
                return time.monotonic() - started
            if response.status_code in (401, 403):
                raise PermissionError(f"endpoint rejected credentials ({response.status_code})")
        except httpx.TransportError:
            pass
        sleep(poll_seconds)


class ServingStatus:
    """Combine cached container counts with the state of an explicit warm-up."""

    def __init__(
        self,
        settings: Settings,
        stats_reader: Callable[[str], ContainerStats] = modal_container_stats,
        warm: Callable[[Settings], float] = wait_until_healthy,
        cache_seconds: float = 5,
    ) -> None:
        """Configure where status comes from; nothing is contacted until asked."""
        self._settings = settings
        self._stats_reader = stats_reader
        self._warm = warm
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._stats: ContainerStats | None = None
        self._stats_error: str | None = None
        self._stats_read_at = float("-inf")
        self._warmup: WarmupState = "idle"
        self._warmup_started = 0.0
        self._warmup_seconds: float | None = None
        self._warmup_error: str | None = None
        self._ready_at = float("-inf")

    def snapshot(self) -> StatusSnapshot:
        """Report serving state; reads container counts at most once per cache period."""
        if self._settings.backend_kind == "mock":
            return StatusSnapshot(
                state="simulated",
                containers=None,
                backlog=None,
                detail=None,
                warmup=self._warmup_view(),
            )
        stats, error = self._read_stats()
        with self._lock:
            if (
                stats is not None
                and stats.containers == 0
                and self._warmup == "ready"
                and time.monotonic() - self._ready_at > READY_GRACE_SECONDS
            ):
                # The container scaled down since the warm-up, so its readiness is stale.
                self._warmup = "idle"
            return StatusSnapshot(
                state=self._state(stats, error),
                containers=stats.containers if stats else None,
                backlog=stats.backlog if stats else None,
                detail=error,
                warmup=self._warmup_view(),
            )

    def start_warmup(self) -> StatusSnapshot:
        """Wake the endpoint in the background; a running warm-up is not started twice."""
        if self._settings.backend_kind != "vllm":
            raise ValueError("Warm-up applies only to the vllm backend")
        with self._lock:
            if self._warmup != "warming":
                self._warmup = "warming"
                self._warmup_started = time.monotonic()
                self._warmup_seconds = None
                self._warmup_error = None
                threading.Thread(target=self._run_warmup, daemon=True).start()
        return self.snapshot()

    def _run_warmup(self) -> None:
        try:
            seconds = self._warm(self._settings)
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the UI
            with self._lock:
                self._warmup, self._warmup_error = "failed", f"{type(exc).__name__}: {exc}"
            return
        with self._lock:
            self._warmup, self._warmup_seconds = "ready", round(seconds, 1)
            self._ready_at = time.monotonic()
            # Force a fresh container count so the badge does not show a stale idle state.
            self._stats_read_at = float("-inf")

    def _read_stats(self) -> tuple[ContainerStats | None, str | None]:
        app_name = self._settings.modal_app
        if app_name is None:
            return None, None
        with self._lock:
            if time.monotonic() - self._stats_read_at < self._cache_seconds:
                return self._stats, self._stats_error
        try:
            stats, error = self._stats_reader(app_name), None
        except Exception as exc:  # noqa: BLE001 - status must degrade, never fail the API
            stats, error = None, f"Container status unavailable: {type(exc).__name__}"
        with self._lock:
            self._stats, self._stats_error = stats, error
            self._stats_read_at = time.monotonic()
        return stats, error

    def _state(self, stats: ContainerStats | None, error: str | None) -> ServingState:
        if self._warmup == "warming":
            return "warming"
        if self._settings.modal_app is None:
            return "ready" if self._warmup == "ready" else "unmonitored"
        if stats is None or error is not None:
            return "unknown"
        if self._warmup == "ready":
            return "ready"
        return "idle" if stats.containers == 0 else "running"

    def _warmup_view(self) -> WarmupView:
        elapsed = (
            round(time.monotonic() - self._warmup_started, 1)
            if self._warmup == "warming"
            else self._warmup_seconds
        )
        return WarmupView(state=self._warmup, seconds=elapsed, error=self._warmup_error)
