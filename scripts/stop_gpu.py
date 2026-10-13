"""Stop the Modal GPU containers now instead of waiting for the idle scale-down."""

from fall_detection.config import Settings
from fall_detection.serving_status import ServingStatus


def main() -> None:
    """Shut down and print the resulting container count."""
    settings = Settings.from_env()
    # Shutdown stops the whole Modal app, so every model's requests must be finished.
    first, *others = (ServingStatus(settings, endpoint=item) for item in settings.endpoints)
    snapshot = first.shutdown(tuple(others))
    print(f"GPU shut down; Modal reports {snapshot['containers']} container(s) (may lag briefly)")


if __name__ == "__main__":
    main()
