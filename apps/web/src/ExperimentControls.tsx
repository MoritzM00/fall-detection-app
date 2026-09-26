import type { Capabilities, ExperimentSettings } from "./api";

type Props = {
  capabilities: Capabilities;
  settings: ExperimentSettings;
  busy: boolean;
  onChange: (settings: ExperimentSettings) => void;
};

export function ExperimentControls({ capabilities, settings, busy, onChange }: Props) {
  const baselinePrompt = settings.prompt_text === capabilities.prompt_preset.prompt;
  const baselineGeneration = settings.generation.temperature === capabilities.generation.temperature
    && settings.generation.max_tokens === capabilities.generation.max_tokens;
  return <details className="experiment-controls">
    <summary>Prompt &amp; generation {(!baselinePrompt || !baselineGeneration) && <small className="experimental">Experimental</small>}</summary>
    <div className="setting-row"><label htmlFor="experiment-model">Served model</label><select id="experiment-model" value={settings.model} disabled={busy} onChange={(event) => onChange({ ...settings, model: event.target.value })}>
      {capabilities.models.map((model) => <option key={model}>{model}</option>)}
    </select></div>
    <div className="setting-row"><span>Prompt preset</span><strong>{baselinePrompt ? "Thesis baseline v1" : "Custom prompt"}</strong></div>
    <label className="prompt-label" htmlFor="prompt-text">Resolved prompt</label>
    <textarea id="prompt-text" rows={9} maxLength={16000} value={settings.prompt_text} disabled={busy} onChange={(event) => onChange({ ...settings, prompt_text: event.target.value })} />
    <p className="preview-note">This exact text is sent with the selected frames. The baseline requires “The best answer is: &lt;class_label&gt;”. Custom prompts may also return a single canonical label. Other formats or ambiguous output fail the run; the 16 activity labels stay fixed.</p>
    <div className="setting-row"><label htmlFor="temperature">Temperature</label><input id="temperature" type="number" min="0" max="2" step="0.1" value={Number.isFinite(settings.generation.temperature) ? settings.generation.temperature : ""} disabled={busy} onChange={(event) => onChange({ ...settings, generation: { ...settings.generation, temperature: event.currentTarget.valueAsNumber } })} /></div>
    <div className="setting-row"><label htmlFor="max-tokens">Max tokens</label><input id="max-tokens" type="number" min={capabilities.generation_limits.min_max_tokens} max={capabilities.generation_limits.max_max_tokens} step="1" value={Number.isFinite(settings.generation.max_tokens) ? settings.generation.max_tokens : ""} disabled={busy} onChange={(event) => onChange({ ...settings, generation: { ...settings.generation, max_tokens: event.currentTarget.valueAsNumber } })} /></div>
    <button className="text-button" disabled={busy} onClick={() => onChange({ model: capabilities.models[0], prompt_text: capabilities.prompt_preset.prompt, generation: { ...capabilities.generation } })}>Reset prompt &amp; generation</button>
  </details>;
}
