import { useId } from "react";
import type { Capabilities, ExperimentSettings } from "./api";

type Props = {
  capabilities: Capabilities;
  settings: ExperimentSettings;
  busy: boolean;
  onChange: (settings: ExperimentSettings) => void;
};

export function baselineExperiment(capabilities: Capabilities): ExperimentSettings {
  return { model: capabilities.models[0], prompt_text: capabilities.prompt_preset.prompt, generation: { ...capabilities.generation } };
}

export function isBaselineExperiment(capabilities: Capabilities, settings: ExperimentSettings): boolean {
  return settings.prompt_text === capabilities.prompt_preset.prompt
    && settings.generation.temperature === capabilities.generation.temperature
    && settings.generation.max_tokens === capabilities.generation.max_tokens;
}

export function experimentValid(settings: ExperimentSettings, capabilities: Capabilities | null): boolean {
  const { temperature, max_tokens: maxTokens } = settings.generation;
  return Boolean(settings.prompt_text.trim()) && settings.prompt_text.length <= 16000
    && Number.isFinite(temperature) && temperature >= 0 && temperature <= 2
    && Number.isInteger(maxTokens) && maxTokens >= (capabilities?.generation_limits.min_max_tokens ?? 16)
    && maxTokens <= (capabilities?.generation_limits.max_max_tokens ?? 4096);
}

export function ExperimentControls({ capabilities, settings, busy, onChange }: Props) {
  const prefix = useId();
  const baselinePrompt = settings.prompt_text === capabilities.prompt_preset.prompt;
  return <section className="settings-section experiment-controls">
    <h3>Model &amp; prompt {!isBaselineExperiment(capabilities, settings) && <small className="experimental">Experimental</small>}</h3>
    <div className="setting-row"><label htmlFor={prefix + "-experiment-model"}>Served model</label><select id={prefix + "-experiment-model"} value={settings.model} disabled={busy} onChange={(event) => onChange({ ...settings, model: event.target.value })}>
      {capabilities.models.map((model) => <option key={model}>{model}</option>)}
    </select></div>
    <div className="setting-row"><label htmlFor={prefix + "-temperature"}>Temperature</label><input id={prefix + "-temperature"} type="number" min="0" max="2" step="0.1" value={Number.isFinite(settings.generation.temperature) ? settings.generation.temperature : ""} disabled={busy} onChange={(event) => onChange({ ...settings, generation: { ...settings.generation, temperature: event.currentTarget.valueAsNumber } })} /></div>
    <div className="setting-row"><label htmlFor={prefix + "-max-tokens"}>Max tokens</label><input id={prefix + "-max-tokens"} type="number" min={capabilities.generation_limits.min_max_tokens} max={capabilities.generation_limits.max_max_tokens} step="1" value={Number.isFinite(settings.generation.max_tokens) ? settings.generation.max_tokens : ""} disabled={busy} onChange={(event) => onChange({ ...settings, generation: { ...settings.generation, max_tokens: event.currentTarget.valueAsNumber } })} /></div>
    <div className="prompt-label"><label htmlFor={prefix + "-prompt-text"}>Resolved prompt</label><span>{baselinePrompt ? "Thesis baseline v1" : "Custom prompt"}</span></div>
    <textarea id={prefix + "-prompt-text"} rows={9} maxLength={16000} value={settings.prompt_text} disabled={busy} onChange={(event) => onChange({ ...settings, prompt_text: event.target.value })} />
    <p className="preview-note">This exact text is sent with the frames. The baseline requires “The best answer is: &lt;class_label&gt;”; custom prompts may also return a single canonical label. Other output fails the run, and the 16 activity labels stay fixed.</p>
  </section>;
}
