"""Wake a scale-to-zero inference endpoint and wait until vLLM reports healthy."""

import argparse
import time

import httpx

from fall_detection.config import Settings


def warm(settings: Settings, deadline_seconds: float = 900, poll_seconds: float = 5) -> float:
    """Poll the server health route until it answers 200; return the elapsed seconds."""
    if settings.backend_kind != "vllm":
        raise ValueError("Set FALL_DETECTION_BACKEND_KIND=vllm to warm a GPU endpoint")
    health_url = f"{settings.inference_base_url.removesuffix('/v1')}/health"
    headers = settings.inference_auth_headers()
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if elapsed > deadline_seconds:
            raise TimeoutError(f"endpoint not healthy after {deadline_seconds:.0f} s")
        try:
            # Modal holds the request during a cold start and redirects past ~150 s.
            response = httpx.get(
                health_url,
                headers=headers,
                timeout=deadline_seconds - elapsed,
                follow_redirects=True,
            )
            if response.status_code == 200:
                return time.monotonic() - started
            if response.status_code in (401, 403):
                raise PermissionError(f"endpoint rejected credentials ({response.status_code})")
        except httpx.TransportError:
            pass
        time.sleep(poll_seconds)


def main() -> None:
    """Print how long the endpoint took to become ready."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=900, help="seconds to wait")
    args = parser.parse_args()
    elapsed = warm(Settings.from_env(), deadline_seconds=args.deadline)
    print(f"Inference endpoint ready after {elapsed:.0f} s")


if __name__ == "__main__":
    main()
