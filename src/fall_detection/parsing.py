import re
from typing import cast

from fall_detection.taxonomy import ACTIVITY_LABELS, ActivityLabel

_ANSWER_PATTERN = re.compile(r"^\s*The best answer is:\s*([a-z_]+)\s*$")


class PredictionParseError(ValueError):
    """Raised when generated text is not one unambiguous canonical label."""


def parse_activity_label(raw_response: str, *, allow_bare_label: bool = False) -> ActivityLabel:
    """Parse a fixed-format answer or an explicitly allowed single canonical label."""
    match = _ANSWER_PATTERN.fullmatch(raw_response)
    if match is None:
        candidate = raw_response.strip()
        if allow_bare_label and candidate in ACTIVITY_LABELS:
            return candidate
        raise PredictionParseError("response does not match the required answer format")

    candidate = match.group(1)
    if candidate not in ACTIVITY_LABELS:
        raise PredictionParseError(f"unknown activity label: {candidate}")
    return cast("ActivityLabel", candidate)
