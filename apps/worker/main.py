import logging
import signal
import time

from fall_detection.config import Settings
from fall_detection.inference import InferenceClient
from fall_detection.media import to_video_data_url
from fall_detection.pipeline import run_pipeline
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
        video = repository.get_video(job.video_id)
        if video is None:
            raise ValueError("job video no longer exists")
        video_data_url = to_video_data_url(video, settings.data_dir)
        client = InferenceClient(
            settings.inference_base_url, timeout_seconds=settings.request_timeout_seconds
        )
        result = run_pipeline(
            client=client,
            model=settings.inference_model,
            video_data_url=video_data_url,
            start_seconds=job.start_seconds,
            end_seconds=job.end_seconds,
        )
        repository.complete_job(
            job.id,
            result,
            backend_kind=settings.backend_kind,
            model=settings.inference_model,
            fixture_version=(
                settings.mock_fixture_version if settings.backend_kind == "mock" else None
            ),
        )
        logger.info("completed job %s as %s", job.id, result.label)
    except (RuntimeError, ValueError) as exc:
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
