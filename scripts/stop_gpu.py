"""Stop the Modal GPU containers now instead of waiting for the idle scale-down."""

from fall_detection.config import Settings
from fall_detection.serving_status import ServingStatus


def main() -> None:
    """Shut down and print the resulting container count."""
    snapshot = ServingStatus(Settings.from_env()).shutdown()
    print(f"GPU shut down; Modal reports {snapshot['containers']} container(s) (may lag briefly)")


if __name__ == "__main__":
    main()
