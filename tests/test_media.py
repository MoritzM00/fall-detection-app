from pathlib import Path

import pytest

from fall_detection.media import list_dataset_video_paths, resolve_dataset_video


def test_dataset_video_paths_are_sorted_and_relative(tmp_path: Path) -> None:
    first = tmp_path / "GMDCSA24" / "video" / "Subject_1" / "Fall" / "02.mp4"
    second = tmp_path / "GMDCSA24" / "video" / "Subject_1" / "ADL" / "01.mp4"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.touch()
    second.touch()

    assert list_dataset_video_paths(tmp_path) == [
        "GMDCSA24/video/Subject_1/ADL/01.mp4",
        "GMDCSA24/video/Subject_1/Fall/02.mp4",
    ]


def test_dataset_video_resolution_rejects_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.mp4"
    outside.touch()

    with pytest.raises(ValueError, match="leaves the configured root"):
        resolve_dataset_video(tmp_path, "../outside.mp4")


def test_dataset_video_resolution_requires_existing_mp4(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="was not found"):
        resolve_dataset_video(tmp_path, "missing.mp4")
