"""Wake a scale-to-zero inference endpoint and wait until vLLM reports healthy."""

import argparse

from fall_detection.config import Settings
from fall_detection.serving_status import wait_until_healthy


def main() -> None:
    """Print how long the endpoint took to become ready."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=900, help="seconds to wait")
    args = parser.parse_args()
    elapsed = wait_until_healthy(Settings.from_env(), deadline_seconds=args.deadline)
    print(f"Inference endpoint ready after {elapsed:.0f} s")


if __name__ == "__main__":
    main()
