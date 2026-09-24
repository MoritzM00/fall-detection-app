import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration shared by API and worker processes."""

    data_dir: Path
    database_path: Path
    inference_base_url: str
    inference_model: str
    backend_kind: str
    mock_fixture_version: str
    request_timeout_seconds: float
    upload_max_bytes: int = 512 * 1024 * 1024
    preparation_slots: int = 2
    preparation_wait_seconds: float = 10
    inspection_pngs: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings without performing filesystem I/O."""
        data_dir = Path(os.getenv("FALL_DETECTION_DATA_DIR", "data"))
        database_path = Path(
            os.getenv("FALL_DETECTION_DATABASE_PATH", str(data_dir / "app.sqlite3"))
        )
        return cls(
            data_dir=data_dir,
            database_path=database_path,
            inference_base_url=os.getenv(
                "FALL_DETECTION_INFERENCE_BASE_URL", "http://127.0.0.1:8001/v1"
            ).rstrip("/"),
            inference_model=os.getenv("FALL_DETECTION_INFERENCE_MODEL", "qwen3-vl-8b-instruct"),
            backend_kind=os.getenv("FALL_DETECTION_BACKEND_KIND", "mock"),
            mock_fixture_version=os.getenv("FALL_DETECTION_MOCK_FIXTURE_VERSION", "sample-v1"),
            request_timeout_seconds=float(
                os.getenv("FALL_DETECTION_REQUEST_TIMEOUT_SECONDS", "30")
            ),
            upload_max_bytes=int(
                os.getenv("FALL_DETECTION_UPLOAD_MAX_BYTES", str(512 * 1024 * 1024))
            ),
            preparation_slots=int(os.getenv("FALL_DETECTION_PREPARATION_SLOTS", "2")),
            preparation_wait_seconds=float(
                os.getenv("FALL_DETECTION_PREPARATION_WAIT_SECONDS", "10")
            ),
            inspection_pngs=os.getenv("FALL_DETECTION_INSPECTION_PNGS", "0") == "1",
        )
