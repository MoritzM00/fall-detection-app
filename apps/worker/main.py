import logging
import signal
import time

from fall_detection.config import Settings
from fall_detection.inference import InferenceClient
from fall_detection.media import to_video_data_url
from fall_detection.pipeline import run_pipeline
from fall_detection.preparation import load_prepared_input, load_rgb_frames, to_vllm_jpeg_data_url
from fall_detection.repository import Repository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
stopping = False


def request_stop(_signal_number: int, _frame: object) -> None:
    """Ask the polling loop to stop after its current job."""
    global stopping
    stopping = True


def process_next_job(settings: Settings, repository: Repository) -> bool:
    """Claim and process at most one job, returning whether work was found."""
    job = repository.claim_next_job()
    if job is None:
        return False
    logger.info("processing job %s", job.id)
    try:
        if settings.backend_kind not in {"mock", "vllm"}:
            raise ValueError(f"Unsupported inference backend: {settings.backend_kind}")
        model, prompt = repository.get_inference_configuration(job.configuration_id)
        video = repository.get_video(job.video_id)
        if video is None:
            raise ValueError("job video no longer exists")
        sampled_timestamps = None
        video_metadata = None
        if job.prepared_input_id is not None:
            prepared = load_prepared_input(settings.data_dir, job.prepared_input_id)
            if (
                prepared.video_id != video.id
                or abs(prepared.start_seconds - job.start_seconds) > 0.001
                or abs(prepared.end_seconds - job.end_seconds) > 0.001
            ):
                raise ValueError("Prepared input does not match this job")
            load_rgb_frames(settings.data_dir, prepared)
            video_data_url = to_vllm_jpeg_data_url(settings.data_dir, prepared)
            sampled_timestamps = [frame.actual_seconds for frame in prepared.frames]
            video_metadata = {
                "fps": prepared.fps,
                "frames_indices": list(range(prepared.frame_count)),
                "total_num_frames": prepared.frame_count,
                "duration": prepared.end_seconds - prepared.start_seconds,
                "do_sample_frames": False,
            }
        elif video.source == "synthetic":
            if settings.backend_kind != "mock":
                raise ValueError("Synthetic sample cannot be sent to real vLLM")
            video_data_url = to_video_data_url(video, settings.data_dir)
        else:
            raise ValueError("Real videos require a prepared frame bundle")
        client = InferenceClient(
            settings.inference_base_url, timeout_seconds=settings.request_timeout_seconds
        )
        result = run_pipeline(
            client=client,
            model=model,
            prompt=prompt,
            video_data_url=video_data_url,
            start_seconds=job.start_seconds,
            end_seconds=job.end_seconds,
            sampled_timestamps=sampled_timestamps,
            video_metadata=video_metadata,
        )
        repository.complete_job(
            job.id,
            result,
            backend_kind=settings.backend_kind,
            model=model,
            fixture_version=(
                settings.mock_fixture_version if settings.backend_kind == "mock" else None
            ),
        )
        logger.info("completed job %s as %s", job.id, result.label)
    except (RuntimeError, ValueError, OSError) as exc:
        repository.fail_job(job.id, str(exc))
        logger.error("job %s failed: %s", job.id, exc)
    return True


def main() -> None:
    """Poll the durable queue and process jobs until signalled."""
    settings = Settings.from_env()
    repository = Repository(settings.database_path)
    repository.initialize()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    logger.info("worker ready; backend=%s", settings.backend_kind)
    while not stopping:
        if not process_next_job(settings, repository):
            time.sleep(0.4)


if __name__ == "__main__":
    main()
