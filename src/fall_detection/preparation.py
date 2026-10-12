"""Decode one window, retain the frames sent to online vLLM, and bound concurrent work."""

import hashlib
import json
import math
import shutil
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from fractions import Fraction
from pathlib import Path
from tempfile import mkdtemp
from threading import BoundedSemaphore, Lock
from typing import TypeVar, cast

import av
import numpy as np
from av.container import InputContainer
from av.stream import Stream
from PIL import Image

from fall_detection.models import PreparationRequest, PreparedFrame, PreparedInput, VideoAsset
from fall_detection.storage import storage_lock

PREPROCESSING_VERSION = "pyav-pillow-online-jpeg-v4"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SOURCE_DIGESTS: OrderedDict[tuple[object, ...], str] = OrderedDict()
_SOURCE_DIGESTS_LOCK = Lock()


def _source_key(path: Path) -> tuple[object, ...]:
    """Identify one unchanged source file without reading its content."""
    status = path.stat()
    return (str(path), status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _source_sha256(path: Path) -> tuple[tuple[object, ...], str]:
    """Hash a source once per file identity, so replay windows do not rehash it each time."""
    key = _source_key(path)
    with _SOURCE_DIGESTS_LOCK:
        cached = _SOURCE_DIGESTS.get(key)
        if cached is not None:
            _SOURCE_DIGESTS.move_to_end(key)
            return key, cached
    digest = _sha256_file(path)
    if _source_key(path) != key:
        raise ValueError("Video changed during preparation")
    with _SOURCE_DIGESTS_LOCK:
        _SOURCE_DIGESTS[key] = digest
        while len(_SOURCE_DIGESTS) > 32:
            _SOURCE_DIGESTS.popitem(last=False)
    return key, digest


# Decoding starts at a keyframe at least this far before the first requested timestamp.
# Any keyframe strictly before it suffices for identical selection; the margin keeps a
# window that starts exactly on a keyframe from landing on it and rescanning the file.
SEEK_MARGIN_SECONDS = 1.0


class _UnreliableSeekError(Exception):
    """A seeked decode might differ from the full scan, so the full scan decides."""


def _seeked_frames(
    container: InputContainer, stream: Stream, first: float
) -> Iterator[av.VideoFrame]:
    """Yield the full-scan frame sequence from the last keyframe safely before ``first``.

    Nearest-PTS selection of ``first`` compares the first frame at or after it with
    the frame just before it, so that earlier frame must be decoded too. Frames
    preceding the keyframe in presentation order (open-GOP leading frames) may
    reference the skipped GOP and are discarded; if the keyframe is not strictly
    before ``first`` or presentation order goes back past it, the seek is unreliable.
    """
    target = first - SEEK_MARGIN_SECONDS
    if target <= 0 or stream.start_time is None or stream.time_base is None:
        raise _UnreliableSeekError  # Near the start a full scan costs the same.
    origin = float(stream.start_time * stream.time_base)
    offset = stream.start_time + math.floor(Fraction(target) / stream.time_base)
    container.seek(offset, stream=stream, backward=True, any_frame=False)
    keyframe = None
    for frame in container.decode(stream):
        if not isinstance(frame, av.VideoFrame) or frame.pts is None or frame.time_base is None:
            continue
        position = frame.pts * frame.time_base
        if keyframe is None:
            if not frame.key_frame:
                continue
            if first <= float(position) - origin:
                raise _UnreliableSeekError
            keyframe = position
        elif position < keyframe:
            raise _UnreliableSeekError
        yield frame
    if keyframe is None:
        raise _UnreliableSeekError  # No keyframe before the window was decoded.


def _selected_frames(
    path: Path,
    timestamps: list[float],
    max_actual_seconds: float | None = None,
    *,
    seek: bool = True,
) -> list[tuple[av.VideoFrame, float, int]]:
    """Select nearest frames on the playback timeline, retaining original PTS.

    With ``seek``, decoding starts at a keyframe before the window instead of the
    first frame; any doubt about that seek falls back to the full scan.
    """
    if not timestamps or timestamps[0] < 0:
        raise ValueError("Selected window must contain nonnegative timestamps")
    if seek:
        try:
            return _scan_frames(path, timestamps, max_actual_seconds, seek=True)
        except (_UnreliableSeekError, av.error.FFmpegError):
            pass
    return _scan_frames(path, timestamps, max_actual_seconds, seek=False)


def _scan_frames(
    path: Path, timestamps: list[float], max_actual_seconds: float | None, *, seek: bool
) -> list[tuple[av.VideoFrame, float, int]]:
    selected: list[tuple[av.VideoFrame, float, int]] = []
    with av.open(str(path)) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("Video has no decodable video stream")
        previous: tuple[av.VideoFrame, float, int] | None = None
        origin = (
            float(stream.start_time * stream.time_base)
            if stream.start_time is not None and stream.time_base is not None
            else None
        )
        last_interval = None
        index = 0
        frames = (
            _seeked_frames(container, stream, timestamps[0]) if seek else container.decode(stream)
        )
        for frame in frames:
            if not isinstance(frame, av.VideoFrame) or frame.pts is None or frame.time_base is None:
                continue
            source_seconds = float(frame.pts * frame.time_base)
            if origin is None:
                origin = source_seconds
            seconds = source_seconds - origin
            if previous is None and timestamps[0] < seconds - 1e-6:
                raise ValueError("Selected window starts before decoded video")
            if previous is not None and seconds > previous[1]:
                last_interval = seconds - previous[1]
            current = (frame, seconds, frame.pts)
            while index < len(timestamps) and timestamps[index] <= seconds:
                if max_actual_seconds is not None and seconds > max_actual_seconds + 1e-6:
                    if previous is None:
                        raise ValueError("No available frame at the playback watermark")
                    selected.append(previous)
                elif previous is None or abs(seconds - timestamps[index]) < abs(
                    previous[1] - timestamps[index]
                ):
                    selected.append(current)
                else:
                    selected.append(previous)
                index += 1
            previous = current
            if index == len(timestamps):
                break
        if previous is None:
            raise ValueError("Video has no timestamped frames")
        if index < len(timestamps):
            if seek and last_interval is None:
                raise _UnreliableSeekError  # The full scan may know an earlier frame interval.
            final_frame = previous[0]
            duration = (
                float(final_frame.duration * final_frame.time_base)
                if final_frame.duration is not None
                and final_frame.duration > 0
                and final_frame.time_base is not None
                else last_interval
            )
            if duration is None and stream.average_rate:
                duration = float(1 / stream.average_rate)
            if duration is None and stream.duration is not None and stream.time_base is not None:
                duration = max(0.0, float(stream.duration * stream.time_base) - previous[1])
            end_seconds = previous[1] + (duration or 0.0)
            if timestamps[-1] > end_seconds + 1e-6:
                raise ValueError("Selected window extends beyond decoded video")
        while index < len(timestamps):
            selected.append(previous)
            index += 1
    return selected


def _crop_rgb(frame: av.VideoFrame, size: int) -> np.ndarray:
    return crop_image(Image.fromarray(frame.to_ndarray(format="rgb24")), size)


def crop_image(source: Image.Image, size: int) -> np.ndarray:
    """Center-crop one RGB image exactly as every prepared input is cropped."""
    width, height = source.size
    if min(width, height) == 0:
        raise ValueError("Decoded frame has invalid dimensions")
    scale = size / min(width, height)
    resized = source.resize(
        (max(size, round(width * scale)), max(size, round(height * scale))),
        Image.Resampling.BILINEAR,
    )
    left = (resized.width - size) // 2
    top = (resized.height - size) // 2
    return np.ascontiguousarray(np.asarray(resized.crop((left, top, left + size, top + size))))


def preparation_path(data_dir: Path, prepared_id: str) -> Path:
    """Resolve a content-derived ID without allowing traversal."""
    if len(prepared_id) != 64 or any(char not in "0123456789abcdef" for char in prepared_id):
        raise ValueError("Invalid prepared input ID")
    return data_dir / "prepared" / prepared_id


def load_prepared_input(data_dir: Path, prepared_id: str) -> PreparedInput:
    """Read a stored manifest; its referenced pixels are immutable."""
    path = preparation_path(data_dir, prepared_id) / "manifest.json"
    try:
        return PreparedInput.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Prepared input not found") from exc


def prepare_video(
    video: VideoAsset,
    request: PreparationRequest,
    data_dir: Path,
    inspection_pngs: bool = True,
    *,
    causal: bool = False,
) -> PreparedInput:
    """Prepare a bundle while maintenance cannot remove its source or output."""
    with storage_lock(data_dir, exclusive=False):
        return _prepare_video_unlocked(video, request, data_dir, inspection_pngs, causal)


def _prepare_video_unlocked(
    video: VideoAsset,
    request: PreparationRequest,
    data_dir: Path,
    inspection_pngs: bool,
    causal: bool,
) -> PreparedInput:
    """Store verified RGB arrays and JPEG transport frames; PNGs are optional."""
    if video.id != request.video_id or video.storage_key is None:
        raise ValueError("A stored video is required for frame preparation")
    media_path = (data_dir / video.storage_key).resolve()
    if not media_path.is_relative_to(data_dir.resolve()) or not media_path.is_file():
        raise ValueError("Video media not found")
    end_seconds = request.start_seconds + (request.frame_count - 1) / request.fps
    if end_seconds - request.start_seconds > 30:
        raise ValueError("Prepared windows are limited to 30 seconds")
    version = PREPROCESSING_VERSION + ("-causal" if causal else "")
    source_key, source_sha256 = _source_sha256(media_path)
    identity = json.dumps(
        [
            video.id,
            source_sha256,
            request.start_seconds,
            request.frame_count,
            request.fps,
            request.size,
            version,
        ],
        separators=(",", ":"),
    )
    prepared_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    target = preparation_path(data_dir, prepared_id)
    if (target / "manifest.json").is_file():
        return load_prepared_input(data_dir, prepared_id)

    timestamps = [
        request.start_seconds + index / request.fps for index in range(request.frame_count)
    ]
    selected = _selected_frames(media_path, timestamps, end_seconds if causal else None)
    if _source_key(media_path) != source_key:
        raise ValueError("Video changed during preparation")
    return write_bundle(
        data_dir,
        prepared_id,
        [(_crop_rgb(frame, request.size), actual, pts) for frame, actual, pts in selected],
        timestamps,
        {
            "video_id": video.id,
            "source_sha256": source_sha256,
            "start_seconds": request.start_seconds,
            "end_seconds": end_seconds,
            "frame_count": request.frame_count,
            "fps": request.fps,
            "size": request.size,
            "preprocessing_version": version,
        },
        inspection_pngs,
    )


def write_bundle(
    data_dir: Path,
    prepared_id: str,
    selected: list[tuple[np.ndarray, float, int]],
    timestamps: list[float],
    manifest: dict[str, object],
    inspection_pngs: bool,
) -> PreparedInput:
    """Publish cropped RGB frames, transport JPEGs and their manifest atomically."""
    target = preparation_path(data_dir, prepared_id)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(mkdtemp(prefix=".preparing-", dir=parent))
    try:
        arrays: list[np.ndarray] = []
        frames: list[PreparedFrame] = []
        for index, ((rgb, actual, pts), requested) in enumerate(
            zip(selected, timestamps, strict=True)
        ):
            arrays.append(rgb)
            image = Image.fromarray(rgb)
            if inspection_pngs:
                image.save(temporary / f"{index:02d}.png")
            jpeg_path = temporary / f"{index:02d}.jpg"
            image.save(jpeg_path, format="JPEG", quality=95)
            frames.append(
                PreparedFrame(
                    index=index,
                    requested_seconds=round(requested, 6),
                    actual_seconds=round(actual, 6),
                    source_pts=pts,
                    sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
                    jpeg_sha256=_sha256_file(jpeg_path),
                )
            )
        stack = np.stack(arrays)
        np.save(temporary / "frames.npy", stack, allow_pickle=False)
        prepared = PreparedInput.model_validate(
            {
                **manifest,
                "id": prepared_id,
                "bundle_sha256": hashlib.sha256(stack.tobytes()).hexdigest(),
                "frames": frames,
            }
        )
        (temporary / "manifest.json").write_text(prepared.model_dump_json(), encoding="utf-8")
        if target.exists():
            return load_prepared_input(data_dir, prepared_id)
        try:
            temporary.rename(target)
        except OSError:
            if (target / "manifest.json").is_file():
                return load_prepared_input(data_dir, prepared_id)
            raise
        return prepared
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_rgb_frames(data_dir: Path, prepared: PreparedInput) -> np.ndarray:
    """Verify the stored array before inference or transport."""
    path = preparation_path(data_dir, prepared.id) / "frames.npy"
    try:
        frames = np.load(path, allow_pickle=False)
    except (EOFError, ValueError) as exc:
        raise ValueError("Prepared frame array could not be read") from exc
    expected = (prepared.frame_count, prepared.size, prepared.size, 3)
    if frames.shape != expected or frames.dtype != np.uint8:
        raise ValueError("Prepared frame array has an invalid shape or type")
    if hashlib.sha256(frames.tobytes()).hexdigest() != prepared.bundle_sha256:
        raise ValueError("Prepared frame array failed integrity check")
    return frames


def to_vllm_jpeg_data_url(data_dir: Path, prepared: PreparedInput) -> str:
    """Send the exact JPEG files displayed by the UI to online vLLM."""
    import base64

    load_rgb_frames(data_dir, prepared)
    encoded: list[str] = []
    directory = preparation_path(data_dir, prepared.id)
    for frame in prepared.frames:
        jpeg = (directory / f"{frame.index:02d}.jpg").read_bytes()
        if hashlib.sha256(jpeg).hexdigest() != frame.jpeg_sha256:
            raise ValueError("Prepared JPEG frame failed integrity check")
        encoded.append(base64.b64encode(jpeg).decode("ascii"))
    return "data:video/jpeg;base64," + ",".join(encoded)


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
