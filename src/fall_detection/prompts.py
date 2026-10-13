"""Research prompt presets: component text, assembly order and paired sampling settings.

Component text is copied verbatim from `fall-detection-mllm`
`src/falldet/inference/prompts/components.py`, and assembly follows `PromptBuilder.build_prompt`
in `builder.py`. The two presets reproduce the zero-shot configurations
`config/prompt/default.yaml` (with `config/sampling/qwen3_instruct.yaml`) and
`config/prompt/cot.yaml` (with `config/sampling/qwen3_thinking.yaml`, selected by
`config/experiment/zeroshot_cot.yaml`). Labels come from `ACTIVITY_LABELS`, which has the
research `label2idx` order, formatted as the research `bulleted` variant.
"""

from dataclasses import dataclass

from fall_detection.models import GenerationConfiguration
from fall_detection.prediction import ACTIVITY_LABELS

ROLE_STANDARD = """Role:
You are an expert Human Activity Recognition (HAR) specialist."""

TASK_STANDARD = """Task:
Analyze the video clip and classify the primary action being performed.
Assign exactly one label from the allowed list below."""

TASK_CLIP_OVERLAP_NOTE = """Note that the clip may contain more than one action. If this is the case,
focus on classifying the action in the first part of the clip, not the entire clip.
Example: Clips shows a person jumping and then falling. The correct label is jump."""

COT_INSTRUCTION = """Please reason step-by-step, identify relevant visual content,
analyze key timestamps and clues, and then provide the final answer."""

TEXT_OUTPUT_FORMAT = """Output Format:
Respond with 'The best answer is: <class_label>' where <class_label> is one of the allowed labels."""


def build_prompt(*, clip_overlap_note: bool, chain_of_thought: bool) -> str:
    """Assemble role, task, labels, optional reasoning and the text output format."""
    sections = [ROLE_STANDARD, TASK_STANDARD]
    if clip_overlap_note:
        sections.append(TASK_CLIP_OVERLAP_NOTE)
    sections.append("\n".join(["Allowed Labels:", *(f"- {label}" for label in ACTIVITY_LABELS)]))
    if chain_of_thought:
        sections.append(COT_INSTRUCTION)
    sections.append(TEXT_OUTPUT_FORMAT)
    return "\n\n".join(sections)


@dataclass(frozen=True, slots=True)
class PromptPreset:
    """One named research configuration of prompt text, answer format and sampling."""

    id: str
    title: str
    description: str
    prompt: str
    chain_of_thought: bool
    generation: GenerationConfiguration


# v2: the request sends the video before the prompt text, as research does; v1 sent it after.
ZERO_SHOT = PromptPreset(
    id="research-zeroshot-v2",
    title="Zero-shot",
    description="Answers with one label directly.",
    prompt=build_prompt(clip_overlap_note=True, chain_of_thought=False),
    chain_of_thought=False,
    generation=GenerationConfiguration(temperature=0, top_p=0.8, top_k=20, max_tokens=64, seed=0),
)

ZERO_SHOT_COT = PromptPreset(
    id="research-zeroshot-cot-v2",
    title="Chain of thought",
    description="Reasons step by step, then gives the label.",
    prompt=build_prompt(clip_overlap_note=False, chain_of_thought=True),
    chain_of_thought=True,
    generation=GenerationConfiguration(
        temperature=0.5, top_p=0.95, top_k=20, max_tokens=4096, seed=0
    ),
)

PROMPT_PRESETS = (ZERO_SHOT, ZERO_SHOT_COT)


def default_preset(chain_of_thought: bool = False) -> PromptPreset:
    """Return the preset used when a request omits its prompt or sampling settings."""
    return ZERO_SHOT_COT if chain_of_thought else ZERO_SHOT


def preset_id(prompt_text: str, chain_of_thought: bool) -> str:
    """Name the preset whose exact prompt and answer format match, otherwise ``custom``."""
    for preset in PROMPT_PRESETS:
        if preset.prompt == prompt_text and preset.chain_of_thought == chain_of_thought:
            return preset.id
    return "custom"
