"""Wake a scale-to-zero inference endpoint and wait until vLLM reports healthy."""

import argparse

from fall_detection.config import Settings
from fall_detection.serving_status import wait_until_healthy


def main() -> None:
    """Print how long the endpoint took to become ready."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=900, help="seconds to wait")
    parser.add_argument("--model", help="served model to wake (default: the default model)")
    args = parser.parse_args()
    settings = Settings.from_env()
    endpoint = settings.endpoint(args.model) if args.model else None
    elapsed = wait_until_healthy(settings, deadline_seconds=args.deadline, endpoint=endpoint)
    print(f"Inference endpoint ready after {elapsed:.0f} s")


if __name__ == "__main__":
    main()
