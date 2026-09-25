"""Decode one window and retain the frames sent to online vLLM."""

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import mkdtemp

import av
import numpy as np
from PIL import Image

from fall_detection.models import PreparationRequest, PreparedFrame, PreparedInput, VideoAsset
from fall_detection.storage_lock import storage_lock

PREPROCESSING_VERSION = "pyav-pillow-online-jpeg-v2"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_frames(path: Path, timestamps: list[float]) -> list[tuple[av.VideoFrame, float, int]]:
    """Select the nearest decoded PTS frame for each requested media time."""
    selected: list[tuple[av.VideoFrame, float, int]] = []
    with av.open(str(path)) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("Video has no decodable video stream")
        previous: tuple[av.VideoFrame, float, int] | None = None
        index = 0
        for frame in container.decode(stream):
            if not isinstance(frame, av.VideoFrame) or frame.pts is None or frame.time_base is None:
                continue
            seconds = float(frame.pts * frame.time_base)
            current = (frame, seconds, frame.pts)
            while index < len(timestamps) and timestamps[index] <= seconds:
                if previous is None or abs(seconds - timestamps[index]) < abs(
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
        if timestamps[-1] > previous[1] + 0.05:
            raise ValueError("Selected window extends beyond decoded video")
        while index < len(timestamps):
            selected.append(previous)
            index += 1
    return selected


def _crop_rgb(frame: av.VideoFrame, size: int) -> np.ndarray:
    source = Image.fromarray(frame.to_ndarray(format="rgb24"))
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
) -> PreparedInput:
    """Prepare a bundle while maintenance cannot remove its source or output."""
    with storage_lock(data_dir, exclusive=False):
        return _prepare_video_unlocked(video, request, data_dir, inspection_pngs)


def _prepare_video_unlocked(
    video: VideoAsset,
    request: PreparationRequest,
    data_dir: Path,
    inspection_pngs: bool,
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
    source_sha256 = _sha256_file(media_path)
    identity = json.dumps(
        [
            video.id,
            source_sha256,
            request.start_seconds,
            request.frame_count,
            request.fps,
            request.size,
            PREPROCESSING_VERSION,
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
    selected = _selected_frames(media_path, timestamps)
    if _sha256_file(media_path) != source_sha256:
        raise ValueError("Video changed during preparation")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(mkdtemp(prefix=".preparing-", dir=parent))
    try:
        arrays: list[np.ndarray] = []
        frames: list[PreparedFrame] = []
        for index, ((source_frame, actual, pts), requested) in enumerate(
            zip(selected, timestamps, strict=True)
        ):
            rgb = _crop_rgb(source_frame, request.size)
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
        bundle_sha256 = hashlib.sha256(stack.tobytes()).hexdigest()
        prepared = PreparedInput(
            id=prepared_id,
            video_id=video.id,
            source_sha256=source_sha256,
            start_seconds=request.start_seconds,
            end_seconds=end_seconds,
            frame_count=request.frame_count,
            fps=request.fps,
            size=request.size,
            preprocessing_version=PREPROCESSING_VERSION,
            bundle_sha256=bundle_sha256,
            frames=frames,
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
