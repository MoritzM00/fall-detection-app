import logging
import signal
import sqlite3
import time
from collections.abc import Callable
from threading import Event, Thread

from fall_detection.config import Settings
from fall_detection.monitoring import Monitoring
from fall_detection.prediction import InferenceClient, require_encrypted_transport, run_pipeline
from fall_detection.preparation import load_prepared_input, to_vllm_jpeg_data_url
from fall_detection.repository import Repository
from fall_detection.storage import to_video_data_url

logger = logging.getLogger(__name__)
stopping = False
HEARTBEAT_SECONDS = 20.0
IDLE_SECONDS = 0.4
MAX_BACKOFF_SECONDS = 5.0
# Stays well inside the 90 s lease, which the heartbeat keeps renewing meanwhile.
SAVE_RETRY_SECONDS = 60.0
UNEXPECTED_FAILURE = "Unexpected worker error. Retry this run."


def request_stop(_signal_number: int, _frame: object) -> None:
    """Ask the polling loop to stop after its current job."""
    global stopping
    stopping = True


def keep_claim_alive(
    repository: Repository,
    job_id: str,
    claim_token: str,
    stop: Event,
    interval_seconds: float = HEARTBEAT_SECONDS,
) -> None:
    """Renew a job lease until stopped or the claim is lost.

    A database error (for example a transient lock) skips one renewal and tries
    again on the next tick; only a refused renewal ends the heartbeat.
    """
    while not stop.wait(interval_seconds):
        try:
            if not repository.renew_claim(job_id, claim_token):
                logger.warning("lost claim on job %s; stopping heartbeat", job_id)
                return
        except sqlite3.Error:
            logger.warning("lease renewal for job %s failed; retrying", job_id, exc_info=True)


def save_result(save: Callable[[], bool], sleep: Callable[[float], None] | None = None) -> bool:
    """Store a finished result, retrying transient database errors.

    The inference has already run and may have been paid for, so a short lock
    must not discard its result. Each attempt is its own transaction.
    """
    sleep = sleep or time.sleep
    delay, waited = IDLE_SECONDS, 0.0
    while True:
        try:
            return save()
        except sqlite3.OperationalError:
            if waited >= SAVE_RETRY_SECONDS:
                raise
            logger.warning("saving result failed; retrying in %.1f s", delay, exc_info=True)
            sleep(delay)
            waited += delay
            delay = min(MAX_BACKOFF_SECONDS, delay * 2)


def process_next_job(settings: Settings, repository: Repository) -> bool:
    """Claim and process at most one job, returning whether work was found."""
    Monitoring(repository).prepare_next(settings)
    job = repository.claim_next_job()
    if job is None:
        return False
    if job.claim_token is None:
        raise RuntimeError("claimed job has no owner token")
    claim_token = job.claim_token
    stop_heartbeat = Event()
    heartbeat_thread = Thread(
        target=keep_claim_alive,
        args=(repository, job.id, claim_token, stop_heartbeat),
        daemon=True,
    )
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
        if config.backend_kind == "vllm":
            require_encrypted_transport(settings.inference_base_url)
        client = InferenceClient(
            settings.inference_base_url,
            timeout_seconds=settings.request_timeout_seconds,
            mock_fixture_identity=config.fixture_version if config.backend_kind == "mock" else None,
            auth_headers=settings.inference_auth_headers(),
        )
        if (
            config.backend_kind == "mock"
            and client.discover_mock_identity() != config.fixture_version
        ):
            raise ValueError("Queued mock fixture identity is unavailable on the serving process")
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
            allow_bare_label=config.prompt_preset == "custom",
        )
        accepted = save_result(
            lambda: repository.complete_job(
                job.id,
                result,
                backend_kind=config.backend_kind,
                model=model,
                fixture_version=(config.fixture_version if config.backend_kind == "mock" else None),
                claim_token=claim_token,
            )
        )
        if accepted:
            logger.info("completed job %s as %s", job.id, result.label)
        else:
            logger.warning("discarded result for expired job %s", job.id)
    except (RuntimeError, ValueError, OSError) as exc:
        repository.fail_job(job.id, str(exc), claim_token)
        logger.error("job %s failed: %s", job.id, exc)
    except Exception:
        # Unexpected errors may carry internal details, so the stored message
        # stays generic and the traceback goes to the worker log only.
        logger.exception("job %s failed unexpectedly", job.id)
        repository.fail_job(job.id, UNEXPECTED_FAILURE, claim_token)
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
    run_loop(settings, repository, lambda: stopping)


def run_loop(
    settings: Settings,
    repository: Repository,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Process jobs until asked to stop, surviving errors in any iteration.

    Errors are logged with their traceback and retried after an exponential
    backoff capped at ``MAX_BACKOFF_SECONDS``; a successful iteration resets it.
    The backoff wait is sliced so a stop request still ends the loop promptly.
    """

    def pause(seconds: float) -> None:
        remaining = seconds
        while remaining > 0 and not should_stop():
            step = min(IDLE_SECONDS, remaining)
            sleep(step)
            remaining -= step

    backoff = 0.0
    while not should_stop():
        try:
            found = process_next_job(settings, repository)
        except Exception:
            backoff = min(MAX_BACKOFF_SECONDS, backoff * 2 if backoff else IDLE_SECONDS)
            logger.exception("worker iteration failed; retrying in %.1f s", backoff)
            pause(backoff)
            continue
        backoff = 0.0
        if not found:
            sleep(IDLE_SECONDS)


if __name__ == "__main__":
    main()
