import base64
from pathlib import Path

from fall_detection.models import VideoAsset

_SYNTHETIC_PAYLOAD = b"fall-detection-synthetic-corridor-v1"


def list_dataset_video_paths(root: Path) -> list[str]:
    """List prepared MP4 files as stable paths relative to the dataset root."""
    if not root.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.mp4"))


def resolve_dataset_video(root: Path, relative_path: str) -> Path:
    """Resolve a catalog path without allowing traversal outside the dataset root."""
    resolved_root = root.resolve()
    candidate = (resolved_root / relative_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("dataset video path leaves the configured root") from exc
    if candidate.suffix.lower() != ".mp4" or not candidate.is_file():
        raise ValueError("dataset video was not found")
    return candidate


def to_video_data_url(asset: VideoAsset, data_dir: Path) -> str:
    """Resolve stored media into the transport shape consumed by inference."""
    if asset.source == "synthetic":
        encoded = base64.b64encode(_SYNTHETIC_PAYLOAD).decode("ascii")
        return f"data:video/mp4;base64,{encoded}"
    if asset.storage_key is None:
        raise ValueError("uploaded asset has no storage key")
    media_path = data_dir / asset.storage_key
    try:
        payload = media_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError("uploaded media is missing") from exc
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:video/mp4;base64,{encoded}"
