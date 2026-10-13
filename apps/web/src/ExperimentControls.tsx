import { useId } from "react";
import type { Capabilities, ExperimentSettings, GenerationSettings, PromptPreset } from "./api";
import { SettingsGroup } from "./SettingsDialog";

const SAMPLING_KEYS = ["temperature", "max_tokens", "top_p", "top_k", "seed"] as const;
const MAX_SEED = 2 ** 31 - 1;

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

/** The research preset these settings reproduce exactly, or null for custom settings. */
export function matchingPreset(capabilities: Capabilities, settings: ExperimentSettings): PromptPreset | null {
  return capabilities.prompt_presets.find((preset) => sameExperiment(settings, experimentFromPreset(capabilities, preset))) ?? null;
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

type Props = {
  capabilities: Capabilities;
  settings: ExperimentSettings;
  busy: boolean;
  onChange: (settings: ExperimentSettings) => void;
};

/** Served model, research prompt strategy and the exact prompt text. */
export function ExperimentSettingsGroup({ capabilities, settings, busy, onChange }: Props) {
  const prefix = useId();
  const presetPrompt = capabilities.prompt_presets.find((preset) => preset.prompt === settings.prompt_text && preset.chain_of_thought === settings.chain_of_thought);
  const strategy = presetFor(capabilities, settings.chain_of_thought);
  const edited = settings.model !== capabilities.models[0] || !presetPrompt;
  return <SettingsGroup index="02" title="Experiment" hint="Model, prompt strategy and the exact prompt text." edited={edited}>
    <div className="setting-row"><label htmlFor={prefix + "-experiment-model"}>Served model</label><select id={prefix + "-experiment-model"} className="model-select" value={settings.model} disabled={busy} onChange={(event) => onChange({ ...settings, model: event.target.value })}>
      {capabilities.models.map((model) => <option key={model}>{model}</option>)}
    </select></div>
    <fieldset className="strategy-picker" disabled={busy}>
      <legend>Prompt strategy</legend>
      <div className="strategy-options">{capabilities.prompt_presets.map((preset) => <label key={preset.id} className="strategy-option">
        <input type="radio" name={prefix + "-strategy"} checked={settings.chain_of_thought === preset.chain_of_thought}
          onChange={() => onChange(experimentFromPreset(capabilities, preset, settings.model))} />
        <span className="strategy-title">{preset.title}</span>
        <span className="strategy-description">{preset.description}</span>
        <span className="strategy-meta">temp {preset.generation.temperature} · ≤ {preset.generation.max_tokens.toLocaleString("en-US")} tokens</span>
      </label>)}</div>
    </fieldset>
    <div className="prompt-label"><label htmlFor={prefix + "-prompt-text"}>Resolved prompt</label><span>{presetPrompt ? presetPrompt.id : `Custom · based on ${strategy.title.toLowerCase()}`}</span></div>
    <textarea id={prefix + "-prompt-text"} rows={10} maxLength={16000} spellCheck={false} value={settings.prompt_text} disabled={busy} onChange={(event) => onChange({ ...settings, prompt_text: event.target.value })} />
    <p className="preview-note">{settings.chain_of_thought
      ? <>This exact text is sent with the frames. The model reasons first; its last line must read “The best answer is: &lt;class_label&gt;”, or the run fails.</>
      : <>This exact text is sent with the frames. The answer must read “The best answer is: &lt;class_label&gt;”; custom prompts may also return a single label. Anything else fails the run.</>}
      {" "}The 16 activity labels stay fixed.</p>
  </SettingsGroup>;
}

type NumberFieldProps = {
  id: string;
  label: string;
  hint: string;
  value: number | null | undefined;
  min: number;
  max: number;
  step: number;
  optional?: boolean;
  disabled: boolean;
  onChange: (value: number | null) => void;
};

function NumberField({ id, label, hint, value, min, max, step, optional = false, disabled, onChange }: NumberFieldProps) {
  return <div className="sampling-field">
    <label htmlFor={id}>{label}</label>
    <input id={id} type="number" min={min} max={max} step={step} value={value != null && Number.isFinite(value) ? value : ""}
      placeholder={optional ? "Server default" : undefined} disabled={disabled}
      onChange={(event) => {
        const next = event.currentTarget.valueAsNumber;
        onChange(Number.isNaN(next) && optional ? null : next);
      }} />
    <small>{hint}</small>
  </div>;
}

type SamplingProps = Props & { budget: { estimate: number; limit: number } | null; contextError: string | null };

/** Decoding parameters sent with each request, plus the context they leave room for. */
export function SamplingSettingsGroup({ capabilities, settings, busy, onChange, budget, contextError }: SamplingProps) {
  const prefix = useId();
  const { generation } = settings;
  const preset = presetFor(capabilities, settings.chain_of_thought);
  const set = (key: keyof GenerationSettings) => (value: number | null) => onChange({ ...settings, generation: { ...generation, [key]: value } });
  return <SettingsGroup index="03" title="LLM sampling" hint={`Research defaults for ${preset.title.toLowerCase()} come from its sampling config.`} edited={!sameGeneration(generation, preset.generation)}>
    <div className="sampling-grid">
      <NumberField id={prefix + "-temperature"} label="Temperature" hint="0 is greedy" value={generation.temperature} min={0} max={2} step={0.1} disabled={busy} onChange={set("temperature")} />
      <NumberField id={prefix + "-top-p"} label="Top-p" hint="Nucleus mass" value={generation.top_p} min={0.01} max={1} step={0.05} optional disabled={busy} onChange={set("top_p")} />
      <NumberField id={prefix + "-top-k"} label="Top-k" hint="−1 turns it off" value={generation.top_k} min={-1} max={1000} step={1} optional disabled={busy} onChange={set("top_k")} />
      <NumberField id={prefix + "-seed"} label="Seed" hint="Fixes sampling noise" value={generation.seed} min={0} max={MAX_SEED} step={1} optional disabled={busy} onChange={set("seed")} />
      <NumberField id={prefix + "-max-tokens"} label="Max tokens" hint={`${capabilities.generation_limits.min_max_tokens}–${capabilities.generation_limits.max_max_tokens.toLocaleString("en-US")} output tokens`} value={generation.max_tokens}
        min={capabilities.generation_limits.min_max_tokens} max={capabilities.generation_limits.max_max_tokens} step={1} disabled={busy} onChange={set("max_tokens")} />
    </div>
    {budget && <ContextBudget {...budget} error={contextError} />}
  </SettingsGroup>;
}

function ContextBudget({ estimate, limit, error }: { estimate: number; limit: number; error: string | null }) {
  return <div className="context-budget" data-over={error !== null}>
    <div><span>Context window</span><strong>≤ {estimate.toLocaleString("en-US")} of {limit.toLocaleString("en-US")} tokens</strong></div>
    <div className="context-budget-bar" role="meter" aria-label="Estimated context use" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={Math.min(estimate, limit)}>
      <i style={{ width: `${Math.min(100, (estimate / limit) * 100)}%` }} />
    </div>
    {error ? <small role="alert">{error}</small> : <small>Conservative estimate of frames, prompt and answer together.</small>}
  </div>;
}
