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
