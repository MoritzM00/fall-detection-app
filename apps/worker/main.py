import logging
import signal
import time
from threading import Event, Thread

from fall_detection.config import Settings
from fall_detection.inference import InferenceClient
from fall_detection.media import to_video_data_url
from fall_detection.pipeline import run_pipeline
from fall_detection.preparation import load_prepared_input, to_vllm_jpeg_data_url
from fall_detection.repository import Repository

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
    if job.claim_token is None:
        raise RuntimeError("claimed job has no owner token")
    claim_token = job.claim_token
    stop_heartbeat = Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(20):
            if not repository.renew_claim(job.id, claim_token):
                return

    heartbeat_thread = Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    logger.info("processing job %s", job.id)
    try:
        config = job.configuration or repository.get_configuration(job.configuration_id)
        if config.backend_kind == "unknown":
            raise ValueError(
                "Legacy run has unknown backend provenance; retry requires a new submission"
            )
        if settings.backend_kind != config.backend_kind:
            raise ValueError(f"Queued backend {config.backend_kind} is unavailable on this worker")
        if (
            config.backend_kind == "mock"
            and config.fixture_version != settings.mock_fixture_version
        ):
            raise ValueError("Queued mock fixture version is unavailable on this worker")
        model, prompt = config.model, config.prompt_text
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
            if config.backend_kind != "mock":
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
            generation=config.generation.model_dump(),
            frame_count=config.preprocessing.frames,
        )
        accepted = repository.complete_job(
            job.id,
            result,
            backend_kind=config.backend_kind,
            model=model,
            fixture_version=(config.fixture_version if config.backend_kind == "mock" else None),
            claim_token=claim_token,
        )
        if accepted:
            logger.info("completed job %s as %s", job.id, result.label)
        else:
            logger.warning("discarded result for expired job %s", job.id)
    except (RuntimeError, ValueError, OSError) as exc:
        repository.fail_job(job.id, str(exc), claim_token)
        logger.error("job %s failed: %s", job.id, exc)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)
    return True


def main() -> None:
    """Poll the durable queue and process jobs until signalled."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
