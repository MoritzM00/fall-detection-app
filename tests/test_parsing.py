import pytest

from fall_detection.parsing import PredictionParseError, parse_activity_label
from fall_detection.taxonomy import ACTIVITY_LABELS


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_parser_accepts_every_canonical_label(label: str) -> None:
    assert parse_activity_label(f"The best answer is: {label}") == label


@pytest.mark.parametrize(
    "response",
    [
        "fall",
        "The best answer is: unknown",
        "The best answer is: fall or fallen",
        "The best answer is: ",
    ],
)
def test_parser_rejects_invalid_output_instead_of_using_other(response: str) -> None:
    with pytest.raises(PredictionParseError):
        parse_activity_label(response)
