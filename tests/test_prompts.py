"""Research prompt presets keep their text, answer format and sampling identity."""

import pytest
from pydantic import ValidationError

from fall_detection.models import GenerationConfiguration
from fall_detection.prediction import ACTIVITY_LABELS
from fall_detection.prompts import (
    COT_INSTRUCTION,
    TASK_CLIP_OVERLAP_NOTE,
    ZERO_SHOT,
    ZERO_SHOT_COT,
    default_preset,
    preset_id,
)


def test_presets_follow_research_section_order_and_list_all_labels_in_order():
    for preset in (ZERO_SHOT, ZERO_SHOT_COT):
        assert preset.prompt.startswith("Role:\nYou are an expert Human Activity Recognition")
        assert preset.prompt.endswith(
            "Respond with 'The best answer is: <class_label>' where "
            "<class_label> is one of the allowed labels."
        )
        listed = [line[2:] for line in preset.prompt.splitlines() if line.startswith("- ")]
        assert listed == list(ACTIVITY_LABELS)


def test_chain_of_thought_preset_drops_the_overlap_note_and_asks_for_reasoning():
    # config/prompt/cot.yaml disables the note that config/prompt/default.yaml enables.
    assert TASK_CLIP_OVERLAP_NOTE in ZERO_SHOT.prompt
    assert TASK_CLIP_OVERLAP_NOTE not in ZERO_SHOT_COT.prompt
    assert COT_INSTRUCTION not in ZERO_SHOT.prompt
    assert ZERO_SHOT_COT.prompt.index(COT_INSTRUCTION) < ZERO_SHOT_COT.prompt.index(
        "Output Format:"
    )


def test_presets_carry_the_research_sampling_configs():
    assert ZERO_SHOT.generation == GenerationConfiguration(
        temperature=0, top_p=0.8, top_k=20, max_tokens=64, seed=0
    )
    assert ZERO_SHOT_COT.generation == GenerationConfiguration(
        temperature=0.5, top_p=0.95, top_k=20, max_tokens=4096, seed=0
    )


def test_preset_identity_requires_exact_text_and_matching_answer_format():
    assert default_preset() is ZERO_SHOT and default_preset(True) is ZERO_SHOT_COT
    assert preset_id(ZERO_SHOT.prompt, False) == ZERO_SHOT.id
    assert preset_id(ZERO_SHOT_COT.prompt, True) == ZERO_SHOT_COT.id
    assert preset_id(ZERO_SHOT_COT.prompt, False) == "custom"
    assert preset_id(ZERO_SHOT.prompt + " ", False) == "custom"


@pytest.mark.parametrize("values", [{"top_k": 0}, {"top_k": -2}, {"top_p": 0}, {"seed": -1}])
def test_generation_rejects_unsupported_sampling_values(values):
    with pytest.raises(ValidationError):
        GenerationConfiguration(temperature=0, max_tokens=64, **values)
