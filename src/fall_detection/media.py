import base64
from pathlib import Path

from fall_detection.models import VideoAsset

_SYNTHETIC_PAYLOAD = b"fall-detection-synthetic-corridor-v1"


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
