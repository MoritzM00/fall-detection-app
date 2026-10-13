import type { ReactNode } from "react";
import type { AnalysisJob, MonitoringSession, MonitoringWindow } from "./api";
import { inFlight, inPaintOrder, resultAge } from "./coverage";
import type { Tone } from "./coverage";
import { formatTokenUsage } from "./tokens";
import { ModelAnswer } from "./SubmittedResult";

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
  return <section className="settings-section"><h3>Activity labels</h3><p className="preview-note">The model chooses one of {labels.length} labels: {labels.map(label => label.replaceAll("_", " ")).join(", ")}.</p></section>;
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
          <span className="history-meta">{state}{window.reason ? ` ${window.reason}` : ""}{historical ? " · historical" : ""}</span>
          {job?.prediction && <strong className={isAlert(job) ? "alert" : ""}>{job.prediction.label.replaceAll("_", " ")}</strong>}
        </summary>
        <p className="preview-note">Generation {window.generation} · window #{window.sequence}{window.sequence_end !== window.sequence ? `–${window.sequence_end}` : ""} · segment {window.segment_id.slice(0, 8)} · {window.reason ?? "No coverage gap recorded"}</p>
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
    <div className="result-overline"><span>{job.state === "succeeded" ? "Activity" : `System state: ${job.state}${job.error ? ` · ${job.error}` : ""}`}</span>{prediction?.backend_kind === "mock" && <span>Simulated</span>}</div>
    {prediction && <>
      <strong className={`result-label ${isAlert(job) ? "alert" : ""}`}>{prediction.label.replaceAll("_", " ")}</strong>
      <p className="simulation-note">Describes the first part of the input window.</p>
      <dl>
        <div><dt>Input interval</dt><dd>{seconds(job.start_seconds)}–{seconds(job.end_seconds)}</dd></div>
        <div><dt>Completed</dt><dd title={prediction.completed_at}>{completed && !Number.isNaN(completed.getTime()) ? completed.toLocaleTimeString() : prediction.completed_at}</dd></div>
      </dl>
      <ModelAnswer prediction={prediction} open={false} />
    </>}
    <details className="result-details"><summary>Details</summary>
      {prediction && <dl>
        <div><dt>Request · pipeline</dt><dd>{prediction.request_duration_ms.toFixed(0)} ms · {prediction.total_duration_ms.toFixed(0)} ms</dd></div>
        <div><dt>Tokens</dt><dd>{formatTokenUsage(prediction)}</dd></div>
      </dl>}
      {prediction && <p className="frame-times">Actual frame timestamps: {prediction.sampled_timestamps.map(seconds).join(", ")}</p>}
      {prediction?.backend_kind === "mock" && <p className="simulation-note">Simulated by fixture <span title={prediction.fixture_version ?? undefined}>{shortId(prediction.fixture_version)}</span>. Local timings include the mock's injected delay; they are not GPU throughput measurements.</p>}
      <p className="raw-response">Job {job.id} · configuration {job.configuration_id} · prepared input {job.prepared_input_id ?? "synthetic"}</p><pre className="saved-prompt">{JSON.stringify(job.configuration, null, 2)}</pre>
    </details>
  </div>;
}

type LatestProps = { job: AnalysisJob | null; session: MonitoringSession; position: number; live: boolean; windows: MonitoringWindow[]; jobs: Record<string, AnalysisJob> };
/**
 * The newest result, dated against the live edge or playback clock, plus the windows still
 * being processed. Every result describes the past: the window has to fill before it is
 * sent, and inference takes time on top of that.
 */
export function LatestResult({ job, session, position, live, windows, jobs }: LatestProps) {
  const freshness = job?.prediction ? resultAge(job.end_seconds, position, { expirationSeconds: session.expiration, strideSeconds: session.stride, processingSeconds: job.prediction.total_duration_ms / 1000 }) : null;
  const age = (value: number) => live ? `${value.toFixed(1)} s ago` : `${value.toFixed(1)} s behind playback`;
  const windowSeconds = (session.configuration.preprocessing.frames - 1) / session.configuration.preprocessing.fps;
  const pending = inFlight(windows, jobs, session);
  return <section className="monitor-latest">
    <h2>Last analyzed</h2>
    {job?.prediction && freshness ? <div className="latest-result" data-outdated={freshness.outdated || undefined}>
      <p className="result-age" role="status">{freshness.outdated
        ? `No current result · this one ended ${age(freshness.age)}`
        : `Input ended ${age(freshness.age)}`}</p>
      <Result job={job} />
    </div> : <div className="result-placeholder"><p>No prediction yet.</p></div>}
    {(pending.length > 0 || session.state === "running") && <ul className="pipeline" aria-label="Windows in progress">
      {pending.map(({ window, stage }) => <li key={window.id}><i data-tone="active" aria-hidden="true" /><span className="history-range">{window.start_seconds.toFixed(1)}–{window.end_seconds.toFixed(1)} s</span><span>{stage}</span></li>)}
      {session.state === "running" && <li><i data-tone="idle" aria-hidden="true" /><span>Filling the next {windowSeconds.toFixed(1)} s window</span></li>}
    </ul>}
  </section>;
}
