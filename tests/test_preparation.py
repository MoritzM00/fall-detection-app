import base64
import hashlib
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import apps.api.main as api
import apps.worker.main as worker
from fall_detection.config import Settings
from fall_detection.inference import InferenceResponse
from fall_detection.models import PreparationRequest
from fall_detection.pipeline import PipelineResult
from fall_detection.preparation import (
    _selected_frames,
    load_rgb_frames,
    preparation_path,
    prepare_video,
    to_vllm_jpeg_data_url,
)
from fall_detection.repository import Repository


def make_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = 64
        stream.height = 32
        stream.pix_fmt = "yuv420p"
        for index in range(12):
            rgb = np.zeros((32, 64, 3), dtype=np.uint8)
            rgb[:, :32, 0] = index * 15
            rgb[:, 32:, 1] = 255 - index * 15
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.fixture
def prepared_video(tmp_path: Path):
    data_dir = tmp_path / "data"
    media_path = data_dir / "media" / "test.mp4"
    make_video(media_path)
    repository = Repository(data_dir / "app.sqlite3")
    repository.initialize()
    video = repository.create_uploaded_video("test.mp4", "media/test.mp4")
    request = PreparationRequest(video_id=video.id, start_seconds=0, frame_count=6, fps=5, size=224)
    prepared = prepare_video(video, request, data_dir)
    return data_dir, repository, video, request, prepared


def test_prepared_pixels_and_online_frame_sequence(prepared_video):
    data_dir, _, video, request, prepared = prepared_video
    frames = load_rgb_frames(data_dir, prepared)
    assert frames.shape == (6, 224, 224, 3)
    assert prepared.end_seconds == 1
    assert [frame.requested_seconds for frame in prepared.frames] == [0, 0.2, 0.4, 0.6, 0.8, 1]
    for index, manifest_frame in enumerate(prepared.frames):
        assert hashlib.sha256(frames[index].tobytes()).hexdigest() == manifest_frame.sha256
        preview = np.asarray(
            Image.open(preparation_path(data_dir, prepared.id) / f"{index:02d}.png")
        )
        assert np.array_equal(preview, frames[index])
    sequence = to_vllm_jpeg_data_url(data_dir, prepared)
    encoded_frames = sequence.removeprefix("data:video/jpeg;base64,").split(",")
    assert len(encoded_frames) == 6
    for index, encoded in enumerate(encoded_frames):
        jpeg = base64.b64decode(encoded)
        assert hashlib.sha256(jpeg).hexdigest() == prepared.frames[index].jpeg_sha256
        assert jpeg == (preparation_path(data_dir, prepared.id) / f"{index:02d}.jpg").read_bytes()
    assert prepare_video(video, request, data_dir).id == prepared.id
    changed = prepare_video(video, request.model_copy(update={"fps": 10}), data_dir)
    assert changed.id != prepared.id
    assert changed.end_seconds == 0.5


def test_job_references_prepared_input_and_worker_sends_selected_frames(
    prepared_video, monkeypatch
):
    data_dir, repository, video, _, prepared = prepared_video
    monkeypatch.setattr(worker.InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    settings = replace(
        Settings.from_env(), data_dir=data_dir, database_path=data_dir / "app.sqlite3"
    )
    with TestClient(api.create_app(settings, repository)) as client:
        prepared_response = client.post(
            "/prepared-inputs",
            json={
                "video_id": video.id,
                "start_seconds": 0,
                "frame_count": 6,
                "fps": 5,
                "size": 224,
            },
        )
        assert prepared_response.status_code == 200
        assert prepared_response.json()["id"] == prepared.id
        missing = client.post(
            "/analysis-jobs", json={"video_id": video.id, "start_seconds": 0, "end_seconds": 1}
        )
        assert missing.status_code == 422
        mismatched = client.post(
            "/analysis-jobs",
            json={
                "video_id": video.id,
                "start_seconds": 0.2,
                "end_seconds": 1.2,
                "prepared_input_id": prepared.id,
                "frame_count": 6,
                "fps": 5,
                "size": 224,
            },
        )
        assert mismatched.status_code == 422
        frame_response = client.get(f"/prepared-inputs/{prepared.id}/frames/0")
        assert frame_response.status_code == 200
        assert (
            frame_response.content
            == (preparation_path(data_dir, prepared.id) / "00.jpg").read_bytes()
        )
        job_response = client.post(
            "/analysis-jobs",
            json={
                "video_id": video.id,
                "start_seconds": 0,
                "end_seconds": 1,
                "prepared_input_id": prepared.id,
                "frame_count": 6,
                "fps": 5,
                "size": 224,
            },
        )
        assert job_response.status_code == 202
    job_id = job_response.json()["id"]
    assert job_response.json()["prepared_input_id"] == prepared.id

    sent = {}

    def fake_completion(_client, payload):
        sent.update(payload)
        return InferenceResponse("The best answer is: fall", "test-completion")

    monkeypatch.setattr(worker.InferenceClient, "complete", fake_completion)
    assert worker.process_next_job(settings, repository)
    video_url = sent["messages"][0]["content"][1]["video_url"]["url"]
    assert video_url == to_vllm_jpeg_data_url(data_dir, prepared)
    assert sent["media_io_kwargs"]["video"]["fps"] == 5
    assert sent["media_io_kwargs"]["video"]["frames_indices"] == list(range(6))
    assert sent["media_io_kwargs"]["video"]["do_sample_frames"] is False
    assert repository.get_job(job_id).prediction.sampled_timestamps == [
        frame.actual_seconds for frame in prepared.frames
    ]

    real_job = repository.create_job(
        video.id,
        0,
        1,
        prepared_input_id=prepared.id,
        model="qwen3-vl-8b-instruct",
        backend_kind="vllm",
        preprocessing={"frames": 6, "fps": 5, "resize": 224, "crop": "center"},
    )
    assert worker.process_next_job(replace(settings, backend_kind="vllm"), repository)
    assert repository.get_job(real_job.id).state == "succeeded"


def test_tampered_frame_array_is_rejected(prepared_video):
    data_dir, _, _, _, prepared = prepared_video
    path = preparation_path(data_dir, prepared.id) / "frames.npy"
    frames = np.load(path, allow_pickle=False)
    frames[0, 0, 0, 0] ^= 1
    np.save(path, frames, allow_pickle=False)
    with pytest.raises(ValueError, match="integrity"):
        load_rgb_frames(data_dir, prepared)


def test_identical_uploads_keep_their_own_prepared_identity(prepared_video):
    data_dir, repository, video, request, first = prepared_video
    second_video = repository.create_uploaded_video("copy.mp4", video.storage_key)
    second_request = request.model_copy(update={"video_id": second_video.id})

    second = prepare_video(second_video, second_request, data_dir)

    assert second.video_id == second_video.id
    assert second.id != first.id
    assert second.bundle_sha256 == first.bundle_sha256
    assert prepare_video(video, request, data_dir) == first
    assert prepare_video(second_video, second_request, data_dir) == second


@pytest.mark.parametrize("contents", [b"", b"invalid array"])
def test_invalid_frame_array_fails_job_without_stopping_worker(prepared_video, contents):
    data_dir, repository, video, _, prepared = prepared_video
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        database_path=data_dir / "app.sqlite3",
        backend_kind="mock",
    )
    job = repository.create_job(video.id, 0, 1, prepared_input_id=prepared.id)
    (preparation_path(data_dir, prepared.id) / "frames.npy").write_bytes(contents)

    assert worker.process_next_job(settings, repository)

    failed = repository.get_job(job.id)
    assert failed.state == "failed"
    assert failed.prediction is None
    assert "frame array" in failed.error
    assert not worker.process_next_job(settings, repository)


def test_tampered_jpeg_fails_job_before_inference(prepared_video, monkeypatch):
    data_dir, repository, video, _, prepared = prepared_video
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        database_path=data_dir / "app.sqlite3",
        backend_kind="mock",
    )
    job = repository.create_job(video.id, 0, 1, prepared_input_id=prepared.id)
    (preparation_path(data_dir, prepared.id) / "00.jpg").write_bytes(b"corrupted")

    def unexpected_request(**kwargs):
        raise AssertionError("Corrupted frames must not reach inference")

    monkeypatch.setattr(worker, "run_pipeline", unexpected_request)
    assert worker.process_next_job(settings, repository)
    failed = repository.get_job(job.id)
    assert failed.state == "failed"
    assert failed.prediction is None
    assert "JPEG frame failed integrity check" in failed.error


@pytest.mark.parametrize("origin", [-5, 0, 5])
@pytest.mark.parametrize("has_start", [True, False])
def test_playback_timeline_retains_pts_and_nearest_frames(tmp_path, monkeypatch, origin, has_start):
    frames = []
    for index in range(12):
        frame = av.VideoFrame(64, 32, "rgb24")
        frame.pts = origin * 10 + index
        frame.time_base = Fraction(1, 10)
        frame.duration = 1
        frames.append(frame)
    stream = SimpleNamespace(
        type="video", start_time=origin * 10 if has_start else None, time_base=Fraction(1, 10)
    )

    class Container:
        streams = [stream]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def decode(self, _stream):
            return iter(frames)

    monkeypatch.setattr(av, "open", lambda *_: Container())
    selected = _selected_frames(tmp_path / "clip.mp4", [0, 0.24, 0.8, 1.0, 1.1])
    assert [item[1] for item in selected] == pytest.approx([0, 0.2, 0.8, 1.0, 1.1])
    assert [item[2] for item in selected] == [origin * 10 + i for i in [0, 2, 8, 10, 11]]
    late = _selected_frames(tmp_path / "clip.mp4", [0.8, 1.0, 1.1])
    assert [item[1] for item in late] == pytest.approx([0.8, 1.0, 1.1])
    with pytest.raises(ValueError, match="beyond decoded"):
        _selected_frames(tmp_path / "clip.mp4", [0, 1.201])


@pytest.mark.parametrize("origin", [0, 5])
def test_offset_encoded_video_selects_same_pixels_and_full_duration(tmp_path, origin):
    path = tmp_path / "clip.mp4"
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
        for index in range(12):
            rgb = np.full((32, 64, 3), index * 15, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.time_base = Fraction(1, 10)
            frame.pts = origin * 10 + index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    selected = _selected_frames(path, [0, 0.24, 0.48, 0.72, 0.96, 1.1])
    assert [item[1] for item in selected] == pytest.approx([0, 0.2, 0.5, 0.7, 1.0, 1.1])
    assert [round(float(item[0].to_ndarray(format="rgb24").mean()) / 15) for item in selected] == [
        0,
        2,
        5,
        7,
        10,
        11,
    ]
    with pytest.raises(ValueError, match="beyond decoded"):
        _selected_frames(path, [0, 1.21])


@pytest.mark.parametrize(
    ("duration", "rate", "stream_duration", "expected_end"),
    [
        (3, None, None, 0.9),
        (0, None, None, 1.0),
        (0, 5, None, 0.2),
        (0, None, 2, 0.2),
        (0, None, None, 0.0),
        (None, None, None, 1.0),
        (None, 5, None, 0.2),
        (None, None, 2, 0.2),
        (None, None, None, 0.0),
    ],
)
def test_final_extent_with_variable_and_missing_durations(
    tmp_path, monkeypatch, duration, rate, stream_duration, expected_end
):
    class MissingDurationFrame(av.VideoFrame):
        @property
        def duration(self):
            return None

    pts = [0, 2, 6] if expected_end >= 0.9 else [0]
    frames = []
    for timestamp in pts:
        frame = av.VideoFrame(64, 32, "rgb24")
        if duration is None:
            frame = MissingDurationFrame(64, 32, "rgb24")
        else:
            frame.duration = duration
        frame.pts, frame.time_base = timestamp, Fraction(1, 10)
        frames.append(frame)
    stream = SimpleNamespace(
        type="video",
        start_time=None,
        time_base=Fraction(1, 10),
        average_rate=rate,
        duration=stream_duration,
    )

    class Container:
        streams = [stream]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def decode(self, _stream):
            return iter(frames)

    monkeypatch.setattr(av, "open", lambda *_: Container())
    selected = _selected_frames(tmp_path / "clip", [0, expected_end])
    assert selected[-1][1] == pytest.approx(pts[-1] / 10)
    with pytest.raises(ValueError, match="beyond decoded"):
        _selected_frames(tmp_path / "clip", [0, expected_end + 0.01])


def test_duration_fit_prepares_manifest_with_playback_times(prepared_video):
    data_dir, _, video, request, _ = prepared_video
    fitted = request.model_copy(update={"frame_count": 16, "fps": 15 / 1.2})
    prepared = prepare_video(video, fitted, data_dir)
    assert prepared.end_seconds == pytest.approx(1.2)
    assert prepared.frames[-1].requested_seconds == 1.2
    assert prepared.frames[-1].actual_seconds == 1.1
    assert prepared.preprocessing_version == "pyav-pillow-online-jpeg-v4"


def test_causal_preparation_does_not_select_future_nearest_pts(prepared_video):
    data_dir, _, video, request, _ = prepared_video
    # 0.08 is closer to the future 0.1 frame than the available 0.0 frame.
    request = request.model_copy(update={"frame_count": 2, "fps": 12.5})
    nearest = prepare_video(video, request, data_dir)
    causal = prepare_video(video, request, data_dir, causal=True)
    assert nearest.frames[-1].actual_seconds == 0.1
    assert causal.frames[-1].actual_seconds == 0
    assert causal.id != nearest.id
    assert all(frame.actual_seconds <= causal.end_seconds for frame in causal.frames)


def test_real_replay_worker_uses_prepared_timestamps_and_verifies_duration(
    prepared_video, monkeypatch
):
    from fall_detection.monitoring import Monitoring, SessionCommand, SessionCreate

    data_dir, repository, video, _, _ = prepared_video
    settings = replace(
        Settings.from_env(), data_dir=data_dir, database_path=data_dir / "app.sqlite3"
    )
    monitor = Monitoring(repository)
    request = SessionCreate(
        video_id=video.id,
        duration_seconds=100,
        frame_count=2,
        fps=12.5,
        size=224,
        stride_seconds=0.08,
    )
    config = monitor.configuration(request, "model", "mock", "sample-v1")
    session = monitor.create(request, config)
    monitor.command(session["id"], SessionCommand(action="start", position_seconds=0.08))
    monitor.tick()
    assert monitor.history(session["id"]) == []  # Source metadata has not been verified.
    sent = {}

    def run(**kwargs):
        sent.update(kwargs)
        return PipelineResult("fall", "fall", kwargs["sampled_timestamps"], 1, 2)

    monkeypatch.setattr(worker, "run_pipeline", run)
    monkeypatch.setattr(worker.InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    assert worker.process_next_job(settings, repository)
    current = monitor.get(session["id"])
    assert current["duration"] == pytest.approx(1.2)
    assert current["duration_verified"] == 1
    job = repository.get_job(current["latest_job_id"])
    assert job.prediction.sampled_timestamps == [0, 0]
    assert sent["sampled_timestamps"] == [0, 0]
    assert job.configuration.preprocessing.version.endswith("-causal")
    assert job.configuration.preprocessing.bundle_sha256
