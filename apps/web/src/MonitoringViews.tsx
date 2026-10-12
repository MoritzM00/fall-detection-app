import type { ReactNode } from "react";
import type { AnalysisJob, MonitoringSession, MonitoringWindow } from "./api";
import { inPaintOrder } from "./coverage";
import type { Tone } from "./coverage";
import { formatTokenUsage } from "./tokens";

export const seconds = (n: number) => `${n.toFixed(3)} s`;
const shortId = (value: string | null) => !value ? "unknown" : value.length > 24 ? `${value.slice(0, 24)}…` : value;
const isAlert = (job: AnalysisJob) => ["fall", "fallen"].includes(job.prediction?.label ?? "");

export function toneOf(state: string, job?: AnalysisJob): Tone {
  if (state === "succeeded") return job && isAlert(job) ? "alert" : "done";
  if (["failed", "cancelled", "disconnected"].includes(state)) return "failed";
  if (["skipped", "stopped"].includes(state)) return "gap";
  if (["pending", "preparing", "submitted", "queued", "running"].includes(state)) return "active";
  return "idle";
}

export function sessionName(item: MonitoringSession) {
  const created = item.created_at ? new Date(item.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : item.id.slice(0, 8);
  return `${created} · ${item.state} · ${item.id.slice(0, 8)}`;
}

type ControlsProps = {
  session: MonitoringSession | null; busy: boolean; connected: boolean; ended: boolean;
  onBegin: (action: "start" | "resume" | "restart") => void; onPause: () => void; onStop: () => void;
};
export function ReplayControls({ session, busy, connected, ended, onBegin, onPause, onStop }: ControlsProps) {
  const fresh = session?.position === 0;
  return <div className="replay-controls">
    <button className="button primary" disabled={busy || !session || !connected || session.state === "running" || session.state === "stopped" || ended} title={ended ? "The recording has ended. Use Restart to replay it." : undefined} onClick={() => onBegin(fresh ? "start" : "resume")}>{fresh ? "Start" : "Resume"}</button>
    <button className="button secondary" disabled={busy || session?.state !== "running"} onClick={onPause}>Pause</button>
    <button className="button secondary" disabled={busy || !session || session.state === "stopped"} onClick={onStop}>Stop</button>
    <button className="button secondary restart" disabled={busy || !session || !connected} onClick={() => onBegin("restart")}>Restart / retry inference</button>
  </div>;
}

export function ActivityLabels({ labels }: { labels: string[] }) {
  return <details className="plain-details"><summary>All {labels.length} activity labels</summary><p>{labels.join(", ")}</p></details>;
}

type HistoryProps = { windows: MonitoringWindow[]; jobs: Record<string, AnalysisJob>; session: MonitoringSession; more: boolean; onLoadOlder: () => void };
export function History({ windows, jobs, session, more, onLoadOlder }: HistoryProps) {
  return <section className="coverage-history"><h2>Coverage and prediction history</h2>
    {windows.length === 0 && <p className="preview-note">Windows appear here as playback passes them.</p>}
    <ul>{windows.map(window => {
      const job = window.job_id ? jobs[window.job_id] : undefined;
      const state = job?.state ?? window.state;
      const historical = window.generation !== session.generation || window.segment_id !== session.segment_id;
      return <li key={window.id} className={historical ? "historical" : ""}><details>
        <summary>
          <i data-tone={toneOf(state, job)} aria-hidden="true" />
          <span className="history-range">{seconds(window.start_seconds)}–{seconds(window.end_seconds)}</span>
          <span className="history-meta">Generation {window.generation} · #{window.sequence}{window.sequence_end !== window.sequence ? `–${window.sequence_end}` : ""} · {state}{window.reason ? ` ${window.reason}` : ""}{historical ? " · historical" : ""}</span>
          {job?.prediction && <strong className={isAlert(job) ? "alert" : ""}>{job.prediction.label.replaceAll("_", " ")}</strong>}
        </summary>
        <p className="preview-note">Segment {window.segment_id.slice(0, 8)} · {window.reason ?? "No coverage gap recorded"}</p>
        {job && <Result job={job} />}
      </details></li>;
    })}</ul>
    {more && <button className="text-button" onClick={onLoadOlder}>Load older history</button>}
  </section>;
}

const legend = [["done", "Non-fall activity"], ["alert", "Fall or fallen"], ["active", "In progress"], ["gap", "Not covered"], ["failed", "Failed"]] as const;
type TimelineProps = { windows: MonitoringWindow[]; jobs: Record<string, AnalysisJob>; from: number; to: number; position: number; partial: boolean; onLoadOlder: () => void; seek: ReactNode };
// The seek input is layered over the bar so the coverage timeline is also the scrubber.
export function CoverageTimeline({ windows, jobs, from, to, position, partial, onLoadOlder, seek }: TimelineProps) {
  if (to <= from) return null;
  const pct = (value: number) => `${Math.min(100, Math.max(0, ((value - from) / (to - from)) * 100))}%`;
  // Bars and legend counts cover only the displayed span (a live timeline scrolls).
  const tones = windows.filter(window => window.end_seconds > from).map(window => {
    const job = window.job_id ? jobs[window.job_id] : undefined;
    return { window, job, tone: toneOf(job?.state ?? window.state, job), start: window.start_seconds };
  });
  const counts = Object.fromEntries(legend.map(([tone]) => [tone, tones.filter(item => item.tone === tone).length]));
  const loadedFrom = windows.length ? Math.min(...windows.map(window => window.start_seconds)) : to;
  return <div className="coverage">
    <div className="coverage-track">
      <div className="coverage-bar" aria-hidden="true">
        {partial && loadedFrom > 0 && <i data-tone="unloaded" style={{ left: 0, width: pct(loadedFrom) }} />}
        {inPaintOrder(tones).map(({ window, tone }) => <i key={window.id} data-tone={tone} style={{ left: pct(window.start_seconds), width: pct(from + window.end_seconds - Math.max(from, window.start_seconds)) }} />)}
        <b style={{ left: pct(position) }} />
      </div>
      {seek}
    </div>
    <div className="window-track-scale"><span>{from.toFixed(1)} s</span>{seek && <span className="seek-hint" aria-hidden="true">Click or drag the timeline to seek</span>}<span>{to.toFixed(1)} s</span></div>
    <ul className="coverage-legend">{legend.filter(([tone]) => counts[tone] > 0).map(([tone, name]) => <li key={tone}><i data-tone={tone} aria-hidden="true" />{name} <span>{counts[tone]}</span></li>)}</ul>
    {partial && <p className="preview-note">Counts and bars cover the newest loaded windows only; earlier history is not loaded. <button className="text-button" onClick={onLoadOlder}>Load older history</button></p>}
  </div>;
}

export function Result({ job }: { job: AnalysisJob }) {
  const prediction = job.prediction;
  const completed = prediction ? new Date(prediction.completed_at) : null;
  return <div className="result-card">
    <div className="result-overline"><span>System state: {job.state}{job.error ? ` · ${job.error}` : ""}</span>{prediction?.backend_kind === "mock" && <span>Simulated</span>}</div>
    {prediction && <>
      <strong className={`result-label ${isAlert(job) ? "alert" : ""}`}>{prediction.label.replaceAll("_", " ")}</strong>
      <p className="simulation-note">Describes the first part of the input window.</p>
      <dl>
        <div><dt>Input interval</dt><dd>{seconds(job.start_seconds)}–{seconds(job.end_seconds)}</dd></div>
        <div><dt>Request · pipeline</dt><dd>{prediction.request_duration_ms.toFixed(0)} ms · {prediction.total_duration_ms.toFixed(0)} ms</dd></div>
        <div><dt>Tokens</dt><dd>{formatTokenUsage(prediction)}</dd></div>
        <div><dt>Completed</dt><dd title={prediction.completed_at}>{completed && !Number.isNaN(completed.getTime()) ? completed.toLocaleTimeString() : prediction.completed_at}</dd></div>
      </dl>
    </>}
    <details className="result-details"><summary>Frames, input and configuration</summary>
      {prediction && <p className="frame-times">Actual frame timestamps: {prediction.sampled_timestamps.map(seconds).join(", ")}</p>}
      {prediction?.backend_kind === "mock" && <p className="simulation-note">Simulated by fixture <span title={prediction.fixture_version ?? undefined}>{shortId(prediction.fixture_version)}</span>. Local timings include the mock's injected delay; they are not GPU throughput measurements.</p>}
      <p className="raw-response">Job {job.id} · configuration {job.configuration_id} · prepared input {job.prepared_input_id ?? "synthetic"}</p><pre className="saved-prompt">{JSON.stringify(job.configuration, null, 2)}</pre>
    </details>
  </div>;
}
