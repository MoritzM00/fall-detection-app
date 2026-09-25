import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from types import FrameType
from typing import Protocol


class ChildProcess(Protocol):
    """Process operations needed by the development launcher."""

    def poll(self) -> int | None:
        """Return the exit code if the child has exited."""
        ...

    def terminate(self) -> None:
        """Request graceful termination."""
        ...

    def kill(self) -> None:
        """Force termination."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Reap the child and return its exit code."""
        ...


def stop_processes(processes: Sequence[ChildProcess], timeout_seconds: float = 5) -> None:
    """Stop every started child and wait for it, even after a forced kill."""
    for process in processes:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.terminate()

    still_running = 0
    for process in processes:
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                process.kill()
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                still_running += 1
    if still_running:
        raise RuntimeError(f"Could not reap {still_running} development process(es)")


def main() -> None:
    """Run the API, worker, mock inference, and Vite frontend together."""
    root = Path(__file__).resolve().parents[1]
    services = [
        (
            "mock inference",
            [sys.executable, "-m", "uvicorn", "apps.mock_inference.main:app", "--port", "8001"],
        ),
        (
            "API",
            [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--port", "8000", "--reload"],
        ),
        ("worker", [sys.executable, "-m", "apps.worker.main"]),
        ("web", ["npm", "run", "dev", "--prefix", "apps/web"]),
    ]
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    previous_handlers: dict[
        signal.Signals, signal.Handlers | Callable[[int, FrameType | None], object]
    ] = {}
    stop_requested = False
    exited_service: tuple[str, int] | None = None

    def request_shutdown(_signal_number: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_shutdown)
        for name, command in services:
            if stop_requested:
                break
            process = subprocess.Popen(command, cwd=root)
            processes.append((name, process))
        while not stop_requested:
            for name, process in processes:
                returncode = process.poll()
                if returncode is not None:
                    exited_service = (name, returncode)
                    break
            if exited_service is not None:
                break
            time.sleep(0.5)
    finally:
        try:
            stop_processes([process for _, process in processes])
        finally:
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)

    if exited_service is not None:
        name, returncode = exited_service
        raise SystemExit(
            f"A development service exited unexpectedly: {name} (exit code {returncode})"
        )


if __name__ == "__main__":
    main()
