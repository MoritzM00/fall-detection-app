"""Run disposable browser tests against the local mock-backed application."""

import os
import shutil
import signal
import socket
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> int:
    """Generate a short video and keep all test state in a temporary directory."""
    root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="fall-detection-browser-") as temporary:
        temporary_path = Path(temporary)
        clip = temporary_path / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=8",
                "-t",
                "3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(clip),
            ],
            check=True,
        )
        short_clip = temporary_path / "short.mp4"
        offset_clip = temporary_path / "offset.mp4"
        for target, offset in [(short_clip, "0"), (offset_clip, "5")]:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=10",
                    "-t",
                    "1.2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-output_ts_offset",
                    offset,
                    "-y",
                    str(target),
                ],
                check=True,
            )
        dataset_clip = (
            temporary_path
            / "data"
            / "omnifall"
            / "videos"
            / "Generated"
            / "Development"
            / "Subject-1"
            / "Session-1"
            / "clip.mp4"
        )
        dataset_clip.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(clip, dataset_clip)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("FALL_DETECTION_", "MOCK_INFERENCE_"))
        }
        # Distinct ephemeral selections; strictPort/uvicorn reject any bind race.
        listeners = [socket.socket() for _ in range(3)]
        try:
            for listener in listeners:
                listener.bind(("127.0.0.1", 0))
            for name, listener in zip(("API", "MOCK", "WEB"), listeners, strict=True):
                environment[f"FALL_DETECTION_{name}_PORT"] = str(listener.getsockname()[1])
        finally:
            for listener in listeners:
                listener.close()
        environment.update(
            FALL_DETECTION_E2E_DATA_DIR=str(temporary_path / "data"),
            FALL_DETECTION_E2E_CLIP=str(clip),
            FALL_DETECTION_E2E_SHORT_CLIP=str(short_clip),
            FALL_DETECTION_E2E_OFFSET_CLIP=str(offset_clip),
        )
        # Invoke Playwright directly: pnpm scripts create a separate process group
        # that would escape ownership when this runner receives a signal.
        process = subprocess.Popen(
            [str(root / "apps/web/node_modules/.bin/playwright"), "test", *sys.argv[1:]],
            cwd=root / "apps/web",
            env=environment,
            start_new_session=True,
        )

        def interrupted(_number: int, _frame: object) -> None:
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            return process.wait()
        finally:
            try:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            finally:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
