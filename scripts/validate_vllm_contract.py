"""Opt-in end-to-end validation against a real, reachable GPU vLLM service."""

import argparse
import json
import socket
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import av
import httpx
import numpy as np

import apps.worker.main as worker
from fall_detection.config import Settings
from fall_detection.models import PreparationRequest
from fall_detection.preparation import prepare_video, to_vllm_jpeg_data_url
from fall_detection.repository import Repository
from fall_detection.taxonomy import ACTIVITY_LABELS


def _generate_clip(path: Path) -> None:
    """Create a small, non-sensitive moving test pattern."""
    path.parent.mkdir(parents=True)
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=8)
        stream.width = 96
        stream.height = 64
        stream.pix_fmt = "yuv420p"
        for index in range(25):
            rgb = np.zeros((64, 96, 3), dtype=np.uint8)
            rgb[:, :, 1] = 40
            rgb[20:44, index * 2 : index * 2 + 24, 0] = 220
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def _server_identity(base_url: str, model: str) -> tuple[str, list[str]]:
    """Read the serving version and model IDs before sending video data."""
    root = base_url.removesuffix("/v1")
    version_response = httpx.get(f"{root}/version", timeout=10)
    version_response.raise_for_status()
    version = version_response.json().get("version")
    models_response = httpx.get(f"{base_url}/models", timeout=10)
    models_response.raise_for_status()
    models = [item["id"] for item in models_response.json()["data"]]
    if not isinstance(version, str) or not version:
        raise ValueError("server did not expose a vLLM version")
    if model not in models:
        raise ValueError(f"requested model is not served; available model IDs: {models}")
    return version, models


def validate(base_url: str, model: str, processor_version: str, timeout: float) -> dict:
    """Verify actual request bytes, accepted response, provenance, and failure state."""
    vllm_version, served_models = _server_identity(base_url, model)
    with TemporaryDirectory(prefix="fall-vllm-contract-") as directory:
        data_dir = Path(directory)
        media_path = data_dir / "media" / "generated.mp4"
        _generate_clip(media_path)
        settings = replace(
            Settings.from_env(),
            data_dir=data_dir,
            database_path=data_dir / "app.sqlite3",
            inference_base_url=base_url,
            inference_model=model,
            backend_kind="vllm",
            request_timeout_seconds=timeout,
        )
        repository = Repository(settings.database_path)
        repository.initialize()
        video = repository.create_uploaded_video("generated.mp4", "media/generated.mp4")
        request = PreparationRequest(
            video_id=video.id,
            start_seconds=0,
            frame_count=16,
            fps=7.5,
            size=224,
        )
        prepared = prepare_video(video, request, data_dir, inspection_pngs=False)
        job = repository.create_job(
            video.id,
            prepared.start_seconds,
            prepared.end_seconds,
            model=model,
            prepared_input_id=prepared.id,
            backend_kind="vllm",
            preprocessing={
                "frames": prepared.frame_count,
                "fps": prepared.fps,
                "resize": prepared.size,
                "crop": "center",
                "version": prepared.preprocessing_version,
                "bundle_sha256": prepared.bundle_sha256,
            },
        )
        expected_url = to_vllm_jpeg_data_url(data_dir, prepared)
        expected_metadata = {
            "fps": prepared.fps,
            "frames_indices": list(range(prepared.frame_count)),
            "total_num_frames": prepared.frame_count,
            "duration": prepared.end_seconds - prepared.start_seconds,
            "do_sample_frames": False,
        }
        original_complete = worker.InferenceClient.complete
        request_checked = False

        def checked_complete(client, payload):
            nonlocal request_checked
            content = payload["messages"][0]["content"]
            video_parts = [part for part in content if part["type"] == "video_url"]
            assert len(video_parts) == 1
            assert video_parts[0]["video_url"]["url"] == expected_url
            assert payload["media_io_kwargs"]["video"] == expected_metadata
            assert payload["model"] == model
            request_checked = True
            return original_complete(client, payload)

        worker.InferenceClient.complete = checked_complete
        try:
            assert worker.process_next_job(settings, repository)
        finally:
            worker.InferenceClient.complete = original_complete
        completed = repository.get_job(job.id)
        if completed is None or completed.state != "succeeded" or completed.prediction is None:
            raise RuntimeError(
                f"live job did not succeed: {completed.error if completed else 'missing'}"
            )
        prediction = completed.prediction
        assert request_checked
        assert prediction.label in ACTIVITY_LABELS
        assert prediction.sampled_timestamps == [frame.actual_seconds for frame in prepared.frames]
        assert prediction.model == model and prediction.backend_kind == "vllm"
        assert prediction.fixture_version is None
        assert completed.prepared_input_id == prepared.id
        assert completed.configuration is not None
        assert completed.configuration.preprocessing.bundle_sha256 == prepared.bundle_sha256

        failure = repository.create_job(
            video.id,
            prepared.start_seconds,
            prepared.end_seconds,
            model=model,
            prepared_input_id=prepared.id,
            backend_kind="vllm",
        )
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            port = unavailable.getsockname()[1]
            failed_settings = replace(
                settings,
                inference_base_url=f"http://127.0.0.1:{port}/v1",
                request_timeout_seconds=0.5,
            )
            assert worker.process_next_job(failed_settings, repository)
        failed = repository.get_job(failure.id)
        assert failed is not None and failed.state == "failed"
        assert failed.prediction is None and failed.error is not None
        assert "inference request failed" in failed.error
        return {
            "vllm_version": vllm_version,
            "processor_version": processor_version,
            "served_model_ids": served_models,
            "requested_model": model,
            "request_exact_jpegs_and_metadata": request_checked,
            "result_label": prediction.label,
            "timestamp_count": len(prediction.sampled_timestamps),
            "configuration_persisted": True,
            "server_failure_state": failed.state,
            "mock_fallback": False,
        }


def main() -> None:
    """Require explicit target identity and print a credential-free summary."""
    if not __debug__:
        raise RuntimeError("contract validation requires Python assertions")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="GPU server OpenAI-compatible /v1 URL")
    parser.add_argument("--model", required=True, help="exact served model ID")
    parser.add_argument(
        "--processor-version",
        required=True,
        help="processor class and Transformers version observed on the GPU host",
    )
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    print(
        json.dumps(
            validate(args.base_url.rstrip("/"), args.model, args.processor_version, args.timeout),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
