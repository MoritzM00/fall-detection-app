"""Preview or apply conservative cleanup of managed uploads, bundles and ended sessions."""

import argparse
from datetime import timedelta

from fall_detection.config import Settings
from fall_detection.retention import PruneReport, prune_media


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def session_summary(report: PruneReport) -> list[str]:
    """One line per ended session: eligible bundles and live frames with their size."""
    usage: dict[str, dict[str, list[int]]] = {}
    for candidate in report.candidates:
        for session_id in candidate.session_ids:
            totals = usage.setdefault(session_id, {}).setdefault(candidate.kind, [0, 0, 0])
            totals[0] += 1
            totals[1] += candidate.file_count
            totals[2] += candidate.size_bytes
    lines = []
    for session_id, kinds in sorted(usage.items()):
        bundles = kinds.get("prepared", [0, 0, 0])
        frames = kinds.get("live", [0, 0, 0])
        lines.append(
            f"session {session_id}: {bundles[0]} prepared bundles ({_megabytes(bundles[2])}), "
            f"{frames[1]} live frames ({_megabytes(frames[2])})"
        )
    return lines


def evaluation_warnings(report: PruneReport, *, applied: bool = False) -> list[str]:
    """Warn about eligible sessions whose real predictions an evaluation may still need.

    After ``--apply`` the advice to export first comes too late, so the warning then
    names only sessions that actually lost an input and says what that means.
    """
    if not applied:
        return [
            f"warning: session {session_id} has real (vLLM) predictions; after pruning, its "
            "export reports input_status unavailable and cannot feed a real evaluation "
            "report. Export its manifest first, or use --keep-sessions while evaluation "
            "is pending."
            for session_id in report.evaluated_sessions
        ]
    removed = set(report.removed)
    pruned = {
        session_id
        for candidate in report.candidates
        if candidate.path in removed
        for session_id in candidate.session_ids
    }
    return [
        f"warning: session {session_id} had real (vLLM) predictions and its inputs were "
        "pruned; its export now reports input_status unavailable and can no longer feed "
        "a real evaluation report."
        for session_id in report.evaluated_sessions
        if session_id in pruned
    ]


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
    parser.add_argument(
        "--session-age-days",
        type=float,
        default=30,
        help="prune prepared inputs and live frames of monitoring sessions stopped "
        "more than this many days ago (default: 30)",
    )
    parser.add_argument(
        "--keep-sessions",
        action="store_true",
        help="leave all monitoring session data in place",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    report = prune_media(
        settings.data_dir,
        settings.database_path,
        min_age=timedelta(hours=args.min_age_hours),
        session_age=None if args.keep_sessions else timedelta(days=args.session_age_days),
        apply=args.apply,
    )
    print(f"Managed data: {settings.data_dir.resolve()}")
    print(f"Mode: {'apply' if args.apply else 'dry run'}")
    for candidate in report.candidates:
        print(
            f"eligible {candidate.kind}: {candidate.path} ({candidate.reason}; "
            f"{candidate.file_count} files, {_megabytes(candidate.size_bytes)})"
        )
    for line in [*session_summary(report), *evaluation_warnings(report, applied=args.apply)]:
        print(line)
    total = sum(candidate.size_bytes for candidate in report.candidates)
    print(
        f"Eligible: {len(report.candidates)} ({_megabytes(total)}); "
        f"removed: {len(report.removed)}; skipped after recheck: {len(report.skipped)}"
    )


if __name__ == "__main__":
    main()
