# Research baseline

Inspected local references on 2026-09-07:

- `/Users/moritz/projects/fall-detection-mllm`
- `/Users/moritz/projects/master-thesis`

These remain external read-only references for this work. No research source or datasets have been copied into this scaffold.

## Verified reference behavior

| Item | Evidence |
| --- | --- |
| Training selects Qwen3-VL-8B-Instruct | Code: `config/training_config.yaml` and `config/model/qwenvl.yaml` |
| Inference uses 16 frames at 7.5 FPS, size 448 | Code: `config/inference_config.yaml` |
| Shortest-edge resize and center crop | Thesis: `content/5_experimental_setup.tex`, preprocessing section |
| Text response and clip-overlap note enabled | Code: `config/prompt/default.yaml` |
| Target is action in first part of clip | Code: `src/falldet/inference/prompts/components.py` |
| Text output more robust in prompt ablations | Thesis: `content/7_conclusion.tex` |
| In-process vLLM and LoRA support exist | Code: `src/falldet/inference/engine.py`, `scripts/vllm_inference.py` |

The generic model YAML defaults to 2B; product configuration must explicitly select 8B. Configuration defaults are not proof of the exact settings used in any particular saved experiment or adapter.

## Prompt presets

Inspected on 2026-10-13. `src/fall_detection/prompts.py` copies the component text from `src/falldet/inference/prompts/components.py` and assembles it in the order of `PromptBuilder.build_prompt` (`builder.py`): role, task, optional clip-overlap note, bulleted labels in `label2idx` order, optional chain-of-thought instruction, text output format.

| App preset | Research configuration | Verified |
| --- | --- | --- |
| `research-zeroshot-v2` | `config/prompt/default.yaml` with `config/sampling/qwen3_instruct.yaml` (temperature 0, top-p 0.8, top-k 20, max tokens 64, seed 0) | Prompt byte-identical to `PromptBuilder.build_prompt()` output; sampling values equal |
| `research-zeroshot-cot-v2` | `config/prompt/cot.yaml` (overlap note off, CoT instruction on) with `config/sampling/qwen3_thinking.yaml` (temperature 0.5, top-p 0.95, top-k 20, max tokens 4096, seed 0), as selected by `config/experiment/zeroshot_cot.yaml` | Prompt byte-identical; sampling values equal |

`config/experiment/zeroshot_cot.yaml` keeps the model YAML's `variant: Instruct`; the research code selects Qwen3-VL-Thinking only through an override (`model.variant=Thinking`). The app offers the Thinking model as a separate served model, and either preset can run on it.

Both research sampling configs also set presence and frequency penalty 0 and repetition penalty 1.0. These are neutral values, so the app does not send them.

The app sends the video before the prompt text, as the research zero-shot turn does (`conversation.py`). Configurations saved before this change, including the `-v1` preset IDs, record `video_first: false` and keep sending the text first.

Known differences that remain unverified:

- **Server defaults.** Research passes complete `SamplingParams` to offline vLLM. The app talks to the OpenAI-compatible server, which may fill unsent parameters, such as the repetition penalty, from the model's `generation_config.json`.
- **Parsing.** The app follows the research answer markers and label word forms, and sets think-tag reasoning aside with either strategy (research: only with CoT), but fails where research would guess: no label (research: `other`) and two different labels (research: the longest). A scratch comparison against `parsers.py` on 6,750 generated responses found no other differences (see [contracts](contracts.md#model-boundary)).

The previous app default, `thesis-baseline-v1`, was a paraphrase of these components rather than the builder output. Runs saved with it keep that preset ID and its prompt text.

## Taxonomy

`walk`, `fall`, `fallen`, `sit_down`, `sitting`, `lie_down`, `lying`, `stand_up`, `standing`, `other`, `kneel_down`, `kneeling`, `squat_down`, `squatting`, `crawl`, `jump`.

Source: `src/falldet/inference/prompts/components.py`. Preserve canonical strings; recover authoritative numeric IDs before introducing numeric labels.

## Migration implications

- Begin without LoRA, as agreed. Adapter weights are on the user's GPU server and are not required for the first milestone.
- Reuse preprocessing, prompt construction, taxonomy, and parsing concepts selectively. Keep dataset evaluation, training, sweep orchestration, and plotting outside application dependencies.
- Preserve text generation initially and construct application JSON in the worker.
- Existing parser fallback to `other` must not hide product processing failures.
- Research sampling uses annotated segments; continuous monitoring has no ground-truth boundaries. Evaluate this distribution change explicitly.
- A two-second window gives delayed information about its beginning. Do not relabel the result as the action at completion time.
- Center cropping can exclude people near frame edges; assess this before changing preprocessing or deploying to new scenes.

## Serving references

- [vLLM API server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM video input](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)

Pin and test the actual server version during implementation. Online media transport and preprocessing parity have not yet been tested.
