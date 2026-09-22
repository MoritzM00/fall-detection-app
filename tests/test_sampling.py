import pytest

from fall_detection.sampling import sample_timestamps


def test_sample_timestamps_preserve_window_edges() -> None:
    timestamps = sample_timestamps(4.0, 6.0)
    assert len(timestamps) == 16
    assert timestamps[0] == 4.0
    assert timestamps[-1] == 6.0


def test_sample_timestamps_reject_invalid_window() -> None:
    with pytest.raises(ValueError):
        sample_timestamps(2.0, 2.0)
