import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    """Run the API, worker, mock inference, and Vite frontend together."""
    root = Path(__file__).resolve().parents[1]
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "apps.mock_inference.main:app", "--port", "8001"],
            cwd=root,
        ),
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--port", "8000", "--reload"],
            cwd=root,
        ),
        subprocess.Popen([sys.executable, "-m", "apps.worker.main"], cwd=root),
        subprocess.Popen(["npm", "run", "dev", "--prefix", "apps/web"], cwd=root),
    ]

    def stop_all(_signal_number: int, _frame: object) -> None:
        for process in processes:
            process.terminate()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
    finally:
        stop_all(0, object())
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        failed = [
            process.returncode for process in processes if process.returncode not in {0, -2, -15}
        ]
        if failed:
            raise SystemExit(f"A development process exited unexpectedly: {failed}")


if __name__ == "__main__":
    main()
