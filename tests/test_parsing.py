import pytest

from fall_detection.parsing import PredictionParseError, parse_activity_label
from fall_detection.taxonomy import ACTIVITY_LABELS


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_parser_accepts_every_canonical_label(label: str) -> None:
    assert parse_activity_label(f"The best answer is: {label}") == label


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_custom_parser_accepts_single_canonical_labels_without_relaxing_baseline(label):
    assert parse_activity_label(f" \n{label}\n", allow_bare_label=True) == label
    assert parse_activity_label(f"The best answer is: {label}", allow_bare_label=True) == label
    with pytest.raises(PredictionParseError):
        parse_activity_label(label)


@pytest.mark.parametrize("response", ["unknown", "walk or fall", '{"label":"walk"}', "walk."])
def test_custom_parser_rejects_ambiguous_or_unsupported_formats(response):
    with pytest.raises(PredictionParseError):
        parse_activity_label(response, allow_bare_label=True)


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
