"""Seeked window decoding must select exactly what the full scan selects, only faster."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from functools import partial
from pathlib import Path
from typing import cast

import av
import numpy as np
import pytest

import fall_detection.preparation as preparation
from fall_detection.config import Settings
from fall_detection.preparation import SEEK_MARGIN_SECONDS, _selected_frames
from fall_detection.repository import Repository

FRAMES, FPS = 16, 7.5
WIDTH = (FRAMES - 1) / FPS


def _image(index: int) -> np.ndarray:
    """Distinct, compressible content per frame so pixel hashes identify frames."""
    rgb = np.zeros((48, 64, 3), dtype=np.uint8)
    rgb[:, :, 0] = (index * 7) % 256
    rgb[(index % 48), :, 1] = 255
    rgb[:, (index * 3) % 64, 2] = 255
    return rgb


def _encode(
    path: Path,
    codec: str,
    pts: list[int],
    time_base: Fraction,
    options: dict[str, str] | None = None,
    gop: int = 20,
) -> None:
    with av.open(str(path), mode="w") as container:
        stream = cast("av.VideoStream", container.add_stream(codec, rate=30))
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.time_base = time_base
        stream.codec_context.gop_size = gop
        stream.options = dict(options or {})
        for index, timestamp in enumerate(pts):
            frame = av.VideoFrame.from_ndarray(_image(index), format="rgb24")
            frame.pts, frame.time_base = timestamp, time_base
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def _x264(extra: str = "") -> dict[str, str]:
    params = "keyint=20:min-keyint=20:scenecut=0:bframes=2" + extra
    return {"preset": "veryfast", "x264-params": params}


CFR = list(range(300))  # 10 s at 30 fps, time base 1/30.
# Irregular frame intervals (20-110 ms) in a millisecond time base.
VFR = list(np.cumsum([0] + [(20, 33, 50, 110, 41)[index % 5] for index in range(199)]))

PROFILES = {
    "h264-mp4": ("clip.mp4", "libx264", CFR, Fraction(1, 30), _x264()),
    "h264-open-gop-mkv": ("clip.mkv", "libx264", CFR, Fraction(1, 30), _x264(":open-gop=1")),
    "vp9-webm": ("clip.webm", "libvpx-vp9", CFR, Fraction(1, 30), {"deadline": "realtime"}),
    "h264-vfr-mp4": ("clip.mp4", "libx264", [int(v) for v in VFR], Fraction(1, 1000), _x264()),
}


@pytest.fixture(scope="module", params=sorted(PROFILES))
def clip(request, tmp_path_factory) -> Path:
    name, codec, pts, time_base, options = PROFILES[request.param]
    path = tmp_path_factory.mktemp(request.param) / name
    _encode(path, codec, pts, time_base, options)
    return path


def _seconds(pts: int | None, time_base: Fraction | None) -> float:
    assert pts is not None and time_base is not None
    return float(pts * time_base)


def _timeline(path: Path) -> tuple[list[float], list[float]]:
    """Return keyframe and frame times on the playback timeline (seconds after start)."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        origin = float((stream.start_time or 0) * (stream.time_base or 0))
        keys = sorted(
            _seconds(packet.pts, packet.time_base) - origin
            for packet in container.demux(stream)
            if packet.is_keyframe and packet.pts is not None
        )
    with av.open(str(path)) as container:
        frames = [
            _seconds(frame.pts, frame.time_base) - origin
            for frame in container.decode(container.streams.video[0])
        ]
    return keys, frames


def _signature(selected) -> list[tuple[int, str]]:
    return [
        (pts, hashlib.sha256(frame.to_ndarray(format="rgb24").tobytes()).hexdigest())
        for frame, _, pts in selected
    ]


def test_seeked_selection_matches_full_scan(clip, monkeypatch):
    keys, frames = _timeline(clip)
    late_keys = [key for key in keys if key > SEEK_MARGIN_SECONDS + 1]
    assert len(late_keys) >= 3, "profile needs several keyframes to cover boundaries"
    last, step = frames[-1], 1 / FPS
    starts = {
        0.0,  # First window.
        0.4,  # Too early to seek.
        SEEK_MARGIN_SECONDS + 0.01,  # First window that can seek.
        *late_keys[:2],  # Exactly on a keyframe.
        late_keys[1] - 0.5 * step,  # Nearest frame lies before the keyframe.
        late_keys[1] + 1e-4,
        frames[len(frames) // 2],  # Exactly on a frame.
        last - WIDTH,  # Last frame is the last requested timestamp.
        last - WIDTH + 0.01,  # Last timestamp falls inside the final frame's extent.
    }
    scans: list[bool] = []
    original = preparation._scan_frames

    def spy(*args, seek):
        scans.append(seek)
        return original(*args, seek=seek)

    monkeypatch.setattr(preparation, "_scan_frames", spy)
    for start in sorted(starts):
        stamps = [start + index / FPS for index in range(FRAMES)]
        for watermark in (None, stamps[-1]):
            scans.clear()
            seeked = _selected_frames(clip, stamps, watermark)
            used_seek = scans == [True]
            full = _selected_frames(clip, stamps, watermark, seek=False)
            assert _signature(seeked) == _signature(full), (start, watermark)
            assert [item[1] for item in seeked] == [item[1] for item in full]
            if start > keys[1] + SEEK_MARGIN_SECONDS:
                assert used_seek, (start, scans)  # A late window must not rescan.


def test_seek_falls_back_when_landing_after_window(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    _encode(path, "libx264", CFR, Fraction(1, 30), _x264())
    stamps = [2.5 + index / FPS for index in range(FRAMES)]
    full = _signature(_selected_frames(path, stamps, seek=False))
    original_seek = av.container.InputContainer.seek
    seeks: list[int] = []

    class Late:
        """Simulate a demuxer that seeks to a keyframe after the requested target."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

        def seek(self, offset, **kwargs):
            seeks.append(offset)
            # Land 3 s late in the stream's own time base (MP4 raises it to 1/15360), so
            # the backward seek reaches a keyframe inside the window, not before it.
            late = offset + round(3 / kwargs["stream"].time_base)
            return original_seek(self._inner, late, **kwargs)

    scans: list[bool] = []
    original_scan = preparation._scan_frames

    def spy(*args, seek):
        scans.append(seek)
        return original_scan(*args, seek=seek)

    real_open = av.open
    monkeypatch.setattr(av, "open", lambda *a, **k: Late(real_open(*a, **k)))
    monkeypatch.setattr(preparation, "_scan_frames", spy)
    assert _signature(_selected_frames(path, stamps)) == full
    assert seeks  # The seek was tried and rejected, then the full scan decided.
    assert scans == [True, False]


@pytest.mark.parametrize("seek", [True, False])
def test_late_replay_window_is_submitted_within_expiration(tmp_path, monkeypatch, seek):
    """Each decoded frame costs 10 ms of controlled time, so a full scan would expire."""
    from fall_detection.monitoring import Monitoring, SessionCommand, SessionCreate

    data_dir = tmp_path / "data"
    (data_dir / "media").mkdir(parents=True)
    # 60 s at 30 fps with FFmpeg's default 250-frame GOP. Decoding from the first frame to
    # the 56 s window costs about 17 controlled seconds; from its keyframe about 2.4 s.
    source = data_dir / "media" / "long.mp4"
    _encode(source, "libx264", list(range(1800)), Fraction(1, 30), gop=250)
    clock = [datetime(2026, 10, 12, tzinfo=UTC)]
    repository = Repository(data_dir / "app.sqlite3", clock=lambda: clock[0])
    repository.initialize()
    video = repository.create_uploaded_video("long.mp4", "media/long.mp4")
    settings = replace(
        Settings.from_env(), data_dir=data_dir, database_path=data_dir / "app.sqlite3"
    )
    real_open = av.open
    decoded = [0]

    class Timed:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

        def decode(self, *args, **kwargs):
            for frame in self._inner.decode(*args, **kwargs):
                decoded[0] += 1
                clock[0] += timedelta(milliseconds=10)
                yield frame

    monkeypatch.setattr(av, "open", lambda *a, **k: Timed(real_open(*a, **k)))
    if not seek:  # Control: the previous full-scan behaviour expires the same window.
        monkeypatch.setattr(
            preparation, "_selected_frames", partial(preparation._selected_frames, seek=False)
        )
    monitor = Monitoring(repository)
    request = SessionCreate(
        video_id=video.id,
        duration_seconds=60,
        frame_count=FRAMES,
        fps=FPS,
        size=224,
        stride_seconds=2,
        expiration_seconds=5,
    )
    session = monitor.create(request, monitor.configuration(request, "model", "mock", "s-v1"))
    monitor.command(session["id"], SessionCommand(action="start", position_seconds=55))
    monitor.command(session["id"], SessionCommand(action="position", position_seconds=58))
    assert monitor.prepare_next(settings)
    latest = monitor.history(session["id"])[0]
    assert (latest["start_seconds"], latest["end_seconds"]) == (56, 58)
    if seek:
        assert (latest["state"], latest["reason"]) == ("submitted", None)
        assert latest["job_id"] is not None
        assert decoded[0] < 300  # One GOP plus the window, not the 1,680 frames before it.
    else:
        assert (latest["state"], latest["reason"]) == ("skipped", "expired")
        assert decoded[0] > 1600


def test_source_hash_is_reused_but_changes_during_decoding_are_rejected(tmp_path, monkeypatch):
    from fall_detection.models import PreparationRequest

    data_dir = tmp_path / "data"
    source = data_dir / "media" / "clip.mp4"
    source.parent.mkdir(parents=True)
    _encode(source, "libx264", CFR, Fraction(1, 30), _x264())
    repository = Repository(data_dir / "app.sqlite3")
    repository.initialize()
    video = repository.create_uploaded_video("clip.mp4", "media/clip.mp4")
    hashed: list[Path] = []
    original_hash = preparation._sha256_file

    def counting(path):
        hashed.append(path)
        return original_hash(path)

    monkeypatch.setattr(preparation, "_sha256_file", counting)

    def prepare(start):
        request = PreparationRequest(
            video_id=video.id, start_seconds=start, frame_count=4, fps=FPS, size=224
        )
        return preparation.prepare_video(video, request, data_dir, inspection_pngs=False)

    first, second = prepare(2), prepare(4)
    assert first.source_sha256 == second.source_sha256
    assert [path for path in hashed if path.suffix == ".mp4"] == [source.resolve()]

    original_select = preparation._selected_frames

    def modify_while_decoding(*args, **kwargs):
        selected = original_select(*args, **kwargs)
        with source.open("ab") as handle:
            handle.write(b"\0")
        return selected

    monkeypatch.setattr(preparation, "_selected_frames", modify_while_decoding)
    with pytest.raises(ValueError, match="changed during preparation"):
        prepare(6)
