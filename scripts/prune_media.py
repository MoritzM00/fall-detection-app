"""Preview or apply conservative cleanup of managed upload and preparation storage."""

import argparse
from datetime import timedelta

from fall_detection.config import Settings
from fall_detection.retention import prune_media


def main() -> None:
    """Run a dry-run by default and require --apply for removal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete eligible resources")
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=168,
        help="minimum age of each resource and upload record (default: 168)",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    report = prune_media(
        settings.data_dir,
        settings.database_path,
        min_age=timedelta(hours=args.min_age_hours),
        apply=args.apply,
    )
    print(f"Managed data: {settings.data_dir.resolve()}")
    print(f"Mode: {'apply' if args.apply else 'dry run'}")
    for candidate in report.candidates:
        print(f"eligible {candidate.kind}: {candidate.path} ({candidate.reason})")
    print(
        f"Eligible: {len(report.candidates)}; removed: {len(report.removed)}; "
        f"skipped after recheck: {len(report.skipped)}"
    )


if __name__ == "__main__":
    main()
