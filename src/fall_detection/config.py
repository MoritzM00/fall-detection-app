import json
import os
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


@dataclass(frozen=True, slots=True)
class InferenceEndpoint:
    """One served model: where its OpenAI-compatible API lives and which Modal function runs it."""

    model: str
    base_url: str
    modal_function: str = "serve"
    # Writes its reasoning before every answer (Qwen3-VL-Thinking), so short budgets truncate.
    thinking: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration shared by API and worker processes."""

    data_dir: Path
    database_path: Path
    inference_base_url: str
    inference_model: str
    backend_kind: str
    request_timeout_seconds: float
    upload_max_bytes: int = 512 * 1024 * 1024
    preparation_slots: int = 2
    preparation_wait_seconds: float = 10
    inspection_pngs: bool = False
    # The API fails expired job leases on this timer so reads never take write locks.
    lease_recovery_interval_seconds: float = 15
    # Keep the raw RGB stack (frames.npy) in prepared bundles for parity checks.
    rgb_arrays: bool = False
    # Modal app serving vLLM; enables container status that never wakes the GPU.
    modal_app: str | None = None
    # Context window of the served model (vLLM --max-model-len); None skips the check.
    max_model_len: int | None = None
    inference_api_key: str | None = field(default=None, repr=False)
    inference_proxy_key: str | None = field(default=None, repr=False)
    inference_proxy_secret: str | None = field(default=None, repr=False)
    # Host names the API answers to; anything else gets 400 (DNS rebinding defense).
    allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS
    # Models served next to the default one, each on its own endpoint (vllm only).
    additional_endpoints: tuple[InferenceEndpoint, ...] = ()

    def __post_init__(self) -> None:
        """Refuse two endpoints for one model name, which would make routing ambiguous."""
        if len(set(self.models)) != len(self.models):
            raise ValueError("Each served model needs a unique name")

    @property
    def endpoints(self) -> tuple[InferenceEndpoint, ...]:
        """Every served model; the first is the default."""
        return (
            InferenceEndpoint(self.inference_model, self.inference_base_url),
            *self.additional_endpoints,
        )

    @property
    def models(self) -> tuple[str, ...]:
        """Names of every served model; the first is the default."""
        return tuple(endpoint.model for endpoint in self.endpoints)

    def endpoint(self, model: str) -> InferenceEndpoint:
        """Return where ``model`` is served; ValueError when it is not served."""
        for endpoint in self.endpoints:
            if endpoint.model == model:
                return endpoint
        raise ValueError(f"Model '{model}' is not served by this deployment")

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings without performing filesystem I/O."""
        data_dir = Path(os.getenv("FALL_DETECTION_DATA_DIR", "data"))
        database_path = Path(
            os.getenv("FALL_DETECTION_DATABASE_PATH", str(data_dir / "app.sqlite3"))
        )
        backend_kind = os.getenv("FALL_DETECTION_BACKEND_KIND", "mock")
        return cls(
            data_dir=data_dir,
            database_path=database_path,
            inference_base_url=os.getenv(
                "FALL_DETECTION_INFERENCE_BASE_URL", "http://127.0.0.1:8001/v1"
            ).rstrip("/"),
            inference_model=os.getenv("FALL_DETECTION_INFERENCE_MODEL", "qwen3-vl-8b-instruct"),
            backend_kind=backend_kind,
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
            rgb_arrays=os.getenv("FALL_DETECTION_RGB_ARRAYS", "0") == "1",
            modal_app=os.getenv("FALL_DETECTION_MODAL_APP") or None,
            max_model_len=_positive_int_or_none(os.getenv("FALL_DETECTION_MAX_MODEL_LEN")),
            inference_api_key=os.getenv("FALL_DETECTION_INFERENCE_API_KEY") or None,
            inference_proxy_key=os.getenv("FALL_DETECTION_INFERENCE_PROXY_KEY") or None,
            inference_proxy_secret=os.getenv("FALL_DETECTION_INFERENCE_PROXY_SECRET") or None,
            allowed_hosts=LOOPBACK_HOSTS
            + tuple(
                host.strip()
                for host in os.getenv("FALL_DETECTION_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            ),
            # The mock serves a single model, so extra models apply to real serving only.
            additional_endpoints=_additional_endpoints(
                os.getenv("FALL_DETECTION_ADDITIONAL_MODELS")
            )
            if backend_kind == "vllm"
            else (),
        )

    def inference_auth_headers(self) -> dict[str, str]:
        """Build credential headers for a protected serving endpoint."""
        if (self.inference_proxy_key is None) != (self.inference_proxy_secret is None):
            raise ValueError("Inference proxy key and secret must be configured together")
        headers = {}
        if self.inference_api_key:
            headers["Authorization"] = f"Bearer {self.inference_api_key}"
        if self.inference_proxy_key and self.inference_proxy_secret:
            headers["Modal-Key"] = self.inference_proxy_key
            headers["Modal-Secret"] = self.inference_proxy_secret
        return headers


def _positive_int_or_none(value: str | None) -> int | None:
    """Parse an optional positive integer setting; empty means unset."""
    if not value:
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError("FALL_DETECTION_MAX_MODEL_LEN must be a positive integer")
    return parsed


def _additional_endpoints(value: str | None) -> tuple[InferenceEndpoint, ...]:
    """Parse ``{"model": {"base_url": ..., "modal_function": ..., "thinking": ...}}`` JSON."""
    if not value:
        return ()
    try:
        entries = json.loads(value)
        endpoints = tuple(
            InferenceEndpoint(
                model=model,
                base_url=entry["base_url"].rstrip("/"),
                modal_function=entry.get("modal_function", "serve"),
                thinking=bool(entry.get("thinking", False)),
            )
            for model, entry in entries.items()
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "FALL_DETECTION_ADDITIONAL_MODELS must map each model name to an object with a base_url"
        ) from exc
    return endpoints
