import type { Capabilities, ExperimentSettings, GenerationSettings, PromptPreset } from "./api";

const SAMPLING_KEYS = ["temperature", "max_tokens", "top_p", "top_k", "seed"] as const;
export const MAX_SEED = 2 ** 31 - 1;

export function experimentFromPreset(capabilities: Capabilities, preset: PromptPreset, model = capabilities.models[0]): ExperimentSettings {
  return { model, prompt_text: preset.prompt, chain_of_thought: preset.chain_of_thought, generation: { ...preset.generation } };
}

export function baselineExperiment(capabilities: Capabilities): ExperimentSettings {
  return experimentFromPreset(capabilities, capabilities.prompt_presets[0]);
}

/** The preset with the same answer format, which a reset restores. */
export function presetFor(capabilities: Capabilities, chainOfThought: boolean): PromptPreset {
  return capabilities.prompt_presets.find((preset) => preset.chain_of_thought === chainOfThought) ?? capabilities.prompt_presets[0];
}

export function sameGeneration(left: GenerationSettings, right: GenerationSettings): boolean {
  return SAMPLING_KEYS.every((key) => (left[key] ?? null) === (right[key] ?? null));
}

export function sameExperiment(left: ExperimentSettings, right: ExperimentSettings): boolean {
  return left.model === right.model && left.prompt_text === right.prompt_text
    && Boolean(left.chain_of_thought) === Boolean(right.chain_of_thought) && sameGeneration(left.generation, right.generation);
}

/** The research preset whose prompt and sampling these settings reproduce on any model, or null. */
export function matchingPreset(capabilities: Capabilities, settings: ExperimentSettings): PromptPreset | null {
  return capabilities.prompt_presets.find((preset) => sameExperiment(settings, experimentFromPreset(capabilities, preset, settings.model))) ?? null;
}

/** The distinguishing last part of a served model name, such as "Thinking". */
export function modelLabel(model: string): string {
  const last = model.split(/[-/]/).at(-1) ?? model;
  return last.charAt(0).toUpperCase() + last.slice(1);
}

/** A preset's title, naming the model when it is not the default one. */
export function presetTitle(capabilities: Capabilities, preset: PromptPreset, model: string): string {
  return model === capabilities.models[0] ? preset.title : `${preset.title} · ${modelLabel(model)}`;
}

export function experimentValid(settings: ExperimentSettings, capabilities: Capabilities | null): boolean {
  const { temperature, max_tokens: maxTokens, top_p: topP, top_k: topK, seed } = settings.generation;
  return Boolean(settings.prompt_text.trim()) && settings.prompt_text.length <= 16000
    && Number.isFinite(temperature) && temperature >= 0 && temperature <= 2
    && Number.isInteger(maxTokens) && maxTokens >= (capabilities?.generation_limits.min_max_tokens ?? 16)
    && maxTokens <= (capabilities?.generation_limits.max_max_tokens ?? 4096)
    && (topP == null || (topP > 0 && topP <= 1))
    && (topK == null || (Number.isInteger(topK) && (topK === -1 || (topK >= 1 && topK <= 1000))))
    && (seed == null || (Number.isInteger(seed) && seed >= 0 && seed <= MAX_SEED));
}

export function generationSummary(generation: GenerationSettings): string {
  const parts = [`temperature ${generation.temperature}`];
  if (generation.top_p != null) parts.push(`top-p ${generation.top_p}`);
  if (generation.top_k != null) parts.push(`top-k ${generation.top_k === -1 ? "off" : generation.top_k}`);
  if (generation.seed != null) parts.push(`seed ${generation.seed}`);
  parts.push(`max ${generation.max_tokens} tokens`);
  return parts.join(" · ");
}

export function isThinkingModel(capabilities: Capabilities, model: string): boolean {
  return capabilities.thinking_models?.includes(model) ?? false;
}

// A thinking model reasons before every answer; below this budget it is often cut off.
export const THINKING_MIN_TOKENS = 1024;

/**
 * Settings after choosing another model. Picking a thinking model while the zero-shot
 * preset is unchanged switches to chain of thought, whose budget leaves room to reason.
 */
export function withModel(capabilities: Capabilities, settings: ExperimentSettings, model: string): ExperimentSettings {
  const unchanged = matchingPreset(capabilities, settings);
  if (isThinkingModel(capabilities, model) && unchanged && !unchanged.chain_of_thought) {
    return experimentFromPreset(capabilities, presetFor(capabilities, true), model);
  }
  return { ...settings, model };
}
