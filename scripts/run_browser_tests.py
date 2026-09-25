"""Run disposable browser tests against the local mock-backed application."""

import os
import shutil
import subprocess
import sys
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
        environment = os.environ.copy()
        environment.update(
            FALL_DETECTION_E2E_DATA_DIR=str(temporary_path / "data"),
            FALL_DETECTION_E2E_CLIP=str(clip),
        )
        return subprocess.run(
            ["npm", "run", "test:e2e", "--prefix", "apps/web", "--", *sys.argv[1:]],
            cwd=root,
            env=environment,
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
