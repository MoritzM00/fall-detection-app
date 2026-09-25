type WindowProps = {
  startSeconds: number;
  endSeconds: number;
  frameCount: number;
  fps: number;
  duration: number | null;
  valid: boolean;
  busy: boolean;
  onRangeChange: (start: number, end: number) => void;
  onChangeSource: () => void;
};

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

function trackPosition(seconds: number, duration: number): string {
  return `${Math.min(100, Math.max(0, (seconds / duration) * 100))}%`;
}

export function WindowControls({
  startSeconds, endSeconds, frameCount, fps, duration, valid, busy,
  onRangeChange, onChangeSource,
}: WindowProps) {
  return <>
    <div className="clip-controls">
      <div><span className="control-label">Analysis window</span><strong>{formatTime(startSeconds)} — {formatTime(endSeconds)}</strong></div>
      <div className="range-fields">
        <label>Start (s) <input disabled={busy} aria-invalid={!valid} type="number" min="0" step="0.1" value={startSeconds} onChange={(event) => {
          const next = event.currentTarget.valueAsNumber;
          if (Number.isFinite(next)) onRangeChange(next, next + (frameCount - 1) / fps);
        }} /></label>
        <span>to</span>
        <label>End (s) <input disabled={busy} aria-invalid={!valid} type="number" min={startSeconds + 0.1} max={duration ?? 30} step="0.1" value={endSeconds} onChange={(event) => {
          const next = event.currentTarget.valueAsNumber;
          if (Number.isFinite(next)) onRangeChange(startSeconds, next);
        }} /></label>
      </div>
      <button className="text-button" disabled={busy} onClick={onChangeSource}>Change source</button>
    </div>
    {duration !== null && duration > 0 && <div className="window-track" aria-hidden="true">
      <div className="window-track-bar">
        <span className="window-range" style={{ left: trackPosition(startSeconds, duration), width: trackPosition(endSeconds - startSeconds, duration) }} />
        {Array.from({ length: frameCount }, (_, index) => <i key={index} style={{ left: trackPosition(startSeconds + index / fps, duration) }} />)}
      </div>
      <div className="window-track-scale"><span>0 s</span><span>{duration.toFixed(1)} s</span></div>
    </div>}
    {!valid && <p className="range-error" role="alert">Choose a valid window of up to 30 seconds within the clip.</p>}
  </>;
}

type SettingsProps = {
  frameCount: number;
  fps: number;
  size: number;
  rangeDuration: number;
  isBaselineWindow: boolean;
  busy: boolean;
  onFrameCountChange: (count: number) => void;
  onFpsChange: (fps: number) => void;
  onSizeChange: (size: number) => void;
};

export function SamplingSettings({
  frameCount, fps, size, rangeDuration, isBaselineWindow, busy,
  onFrameCountChange, onFpsChange, onSizeChange,
}: SettingsProps) {
  return <>
    <div className="setting-row"><label htmlFor="frame-count">Frames</label><input id="frame-count" type="number" min="2" max="32" step="1" value={frameCount} disabled={busy} onChange={(event) => {
      const next = event.currentTarget.valueAsNumber;
      if (Number.isInteger(next) && next >= 2 && next <= 32) onFrameCountChange(next);
    }} /></div>
    <div className="setting-row"><label htmlFor="target-fps">Sampling FPS</label><input id="target-fps" type="number" min="0.1" max="30" step="0.1" value={fps} disabled={busy} onChange={(event) => {
      const next = event.currentTarget.valueAsNumber;
      if (Number.isFinite(next) && next > 0 && next <= 30) onFpsChange(next);
    }} /></div>
    <div className="setting-row"><label htmlFor="frame-size">Crop size</label><select id="frame-size" value={size} disabled={busy} onChange={(event) => onSizeChange(Number(event.currentTarget.value))}><option value={224}>224 × 224</option><option value={336}>336 × 336</option><option value={448}>448 × 448</option><option value={672}>672 × 672</option></select></div>
    <div className="setting-row"><span>Preset</span><strong>Thesis baseline v1</strong></div>
    <div className="setting-row"><span>Window</span><strong>{rangeDuration.toFixed(2)} s {(!isBaselineWindow || fps !== 7.5 || frameCount !== 16 || size !== 448) && <small className="experimental">Experimental</small>}</strong></div>
    <p className="preview-note">Frame count and FPS set the window length. Editing the end time recalculates FPS.</p>
  </>;
}
