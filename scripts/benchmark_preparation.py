"""Reproduce preparation timings on disposable, generated video clips."""

import argparse
import hashlib
import json
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import fall_detection.preparation as preparation
from fall_detection.models import PreparationRequest, VideoAsset

PROFILES = (("720p-60s", 1280, 720, 15, 60), ("1080p-120s", 1920, 1080, 10, 120))


def _generate(path: Path, width: int, height: int, fps: int, seconds: int) -> None:
    """Create a moving, non-sensitive H.264 source with fixed time base."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate={fps}",
            "-t",
            str(seconds),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def _worker(source: Path, data_dir: Path, seconds: int) -> dict[str, object]:
    """Time one profile in a separate process so peak RSS is comparable."""
    video = VideoAsset(
        id="benchmark-generated-video",
        filename=source.name,
        source="upload",
        storage_key=source.relative_to(data_dir).as_posix(),
        created_at="2026-01-01T00:00:00+00:00",
    )
    original_hash = preparation._sha256_file
    original_select = preparation._selected_frames
    original_crop = preparation._crop_rgb
    phases: dict[str, list[float]] = {}

    def record(name, func):
        def wrapped(*args):
            start = time.perf_counter()
            result = func(*args)
            phases.setdefault(name, []).append(time.perf_counter() - start)
            return result

        return wrapped

    preparation._sha256_file = record("hash", original_hash)
    preparation._selected_frames = record("decode_select", original_select)
    preparation._crop_rgb = record("crop", original_crop)
    positions = (
        ("early", 1.0),
        ("late", seconds - 4.0),
        ("cache_hit", seconds - 4.0),
        ("rapid_early", 2.0),
        ("rapid_middle", seconds / 2.0),
        ("rapid_late", seconds - 5.0),
    )
    rows = []
    saved = []
    for name, start_seconds in positions:
        phases.clear()
        request = PreparationRequest(
            video_id=video.id,
            start_seconds=start_seconds,
            frame_count=16,
            fps=7.5,
            size=448,
        )
        start = time.perf_counter()
        prepared = preparation.prepare_video(video, request, data_dir, inspection_pngs=False)
        elapsed = time.perf_counter() - start
        hash_seconds = sum(phases.get("hash", []))
        decode_seconds = sum(phases.get("decode_select", []))
        crop_seconds = sum(phases.get("crop", []))
        rows.append(
            {
                "case": name,
                "start_seconds": start_seconds,
                "total_seconds": round(elapsed, 3),
                "hash_seconds": round(hash_seconds, 3),
                "decode_select_seconds": round(decode_seconds, 3),
                "crop_seconds": round(crop_seconds, 3),
                "other_seconds": round(elapsed - hash_seconds - decode_seconds - crop_seconds, 3),
                "source_pts_sha256": hashlib.sha256(
                    json.dumps([frame.source_pts for frame in prepared.frames]).encode()
                ).hexdigest(),
                "bundle_sha256": prepared.bundle_sha256,
            }
        )
        saved.append((request, prepared))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_mib = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024

    # Compare persisted selection and RGB hashes with the existing full-scan nearest-PTS path.
    preparation._sha256_file = original_hash
    preparation._selected_frames = original_select
    preparation._crop_rgb = original_crop
    for request, prepared in saved:
        stamps = [request.start_seconds + index / request.fps for index in range(16)]
        reference = original_select(source, stamps)
        assert [frame.source_pts for frame in prepared.frames] == [item[2] for item in reference]
        assert [frame.sha256 for frame in prepared.frames] == [
            hashlib.sha256(original_crop(item[0], request.size).tobytes()).hexdigest()
            for item in reference
        ]
    assert saved[1][1].id == saved[2][1].id
    return {"cases": rows, "peak_rss_mib": round(peak_rss_mib, 1), "reference_match": True}


def main() -> None:
    """Generate representative clips, measure each in isolation, and print JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=3, metavar=("SOURCE", "DATA_DIR", "SECONDS"))
    args = parser.parse_args()
    if args.worker:
        source, data_dir, seconds = args.worker
        print(json.dumps(_worker(Path(source), Path(data_dir), int(seconds))))
        return
    result = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
        "profiles": [],
    }
    with TemporaryDirectory(prefix="fall-prep-benchmark-") as directory:
        root = Path(directory)
        for name, width, height, fps, seconds in PROFILES:
            data_dir = root / name
            media_dir = data_dir / "media"
            media_dir.mkdir(parents=True)
            source = media_dir / "generated.mp4"
            _generate(source, width, height, fps, seconds)
            completed = subprocess.run(
                [sys.executable, __file__, "--worker", str(source), str(data_dir), str(seconds)],
                capture_output=True,
                text=True,
                check=True,
            )
            result["profiles"].append(
                {
                    "name": name,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "duration_seconds": seconds,
                    "source_bytes": source.stat().st_size,
                    **json.loads(completed.stdout),
                }
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
