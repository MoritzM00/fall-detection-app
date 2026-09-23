import math


def sample_timestamps(
    start_seconds: float, end_seconds: float, frame_count: int = 16
) -> list[float]:
    """Return deterministic timestamps spanning the selected range."""
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or start_seconds < 0:
        raise ValueError("timestamps must be finite and non-negative")
    if end_seconds <= start_seconds:
        raise ValueError("end_seconds must be greater than start_seconds")
    if frame_count < 2:
        raise ValueError("frame_count must be at least 2")
    step = (end_seconds - start_seconds) / (frame_count - 1)
    return [round(start_seconds + index * step, 3) for index in range(frame_count)]
