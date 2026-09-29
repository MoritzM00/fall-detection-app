import { useEffect, useRef, useState } from "react";
import { commandSession, createSession, getJob, getSession, getVideo, getWindows, listSessions } from "./api";
import type { AnalysisJob, Capabilities, MonitoringCommand, MonitoringSession, MonitoringWindow, VideoAsset } from "./api";
import { SourceSelection } from "./SourceSelection";
import { ExperimentControls } from "./ExperimentControls";
import { useSourceSelection } from "./useSourceSelection";
import type { ExperimentSettings } from "./api";

const labels = ["walk", "fall", "fallen", "sit_down", "sitting", "lie_down", "lying", "stand_up", "standing", "other", "kneel_down", "kneeling", "squat_down", "squatting", "crawl", "jump"];
const seconds = (n: number) => `${n.toFixed(3)} s`;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function MonitoringReplay({ capabilities }: { capabilities: Capabilities | null }) {
  const source = useSourceSelection(() => {}, () => {});
  const [sessions, setSessions] = useState<MonitoringSession[]>([]);
  const [id, setId] = useState(() => localStorage.getItem("sentinel-session") ?? "");
  const [session, setSession] = useState<MonitoringSession | null>(null);
  const [video, setVideo] = useState<VideoAsset | null>(null);
  const [windows, setWindows] = useState<MonitoringWindow[]>([]);
  const [jobs, setJobs] = useState<Record<string, AnalysisJob>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [updated, setUpdated] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [position, setPosition] = useState(0);
  const [settings, setSettings] = useState<ExperimentSettings | null>(null);
  const [frames, setFrames] = useState(16);
  const [fps, setFps] = useState(7.5);
  const [size, setSize] = useState(448);
  const media = useRef<HTMLVideoElement>(null);
  const fence = useRef(0);
  const current = useRef<MonitoringSession | null>(null);
  const pending = useRef(false);
  const positionPending = useRef(false);
  const retry = useRef<{ id: string; command: MonitoringCommand } | null>(null);
  const syntheticPosition = useRef(0);
  const initialPosition = useRef(true);
  const hydrated = useRef("");
  const polling = useRef(false);
  const jobCache = useRef<Record<string, AnalysisJob>>({});
  current.current = session;

  useEffect(() => { if (capabilities && !current.current) setSettings({ model: capabilities.models[0], prompt_text: capabilities.prompt_preset.prompt, generation: { ...capabilities.generation } }); }, [capabilities]);

  function adopt(next: MonitoringSession) {
    current.current = next;
    setSession(next);
    if (next.state !== "running") media.current?.pause();
  }
  function select(next: string) {
    const leaving = current.current;
    if (leaving?.state === "running") void commandSession(leaving.id, { action: "pause", command_id: crypto.randomUUID() }).catch(() => {});
    media.current?.pause();
    fence.current++;
    initialPosition.current = true;
    retry.current = null; setError(null);
    current.current = null; jobCache.current = {};
    setSession(null); setVideo(null); setWindows([]); setJobs({}); setConnected(false);
    setId(next); localStorage.setItem("sentinel-session", next);
  }

  async function poll(token: number) {
    if ((pending.current && !positionPending.current) || polling.current) return;
    polling.current = true;
    try {
      const all = await listSessions();
      if (token !== fence.current) return;
      setSessions(all);
      if (!id) { setConnected(true); return; }
      const next = await getSession(id);
      const history = await getWindows(id);
      const asset = await getVideo(next.video_id);
      const jobIds = [...new Set([...history.flatMap(w => w.job_id ? [w.job_id] : []), ...(next.latest_job_id ? [next.latest_job_id] : [])])];
      const loaded = await Promise.all(jobIds.filter(jobId => {
        const cached = jobCache.current[jobId];
        return !cached || ["queued", "running"].includes(cached.state);
      }).map(getJob));
      if (token !== fence.current || (pending.current && !positionPending.current)) return;
      if (current.current && (next.generation !== current.current.generation || next.segment_id !== current.current.segment_id)) { media.current?.pause(); initialPosition.current = true; }
      if (current.current?.generation === next.generation && current.current.segment_id === next.segment_id) next.position = Math.max(next.position, current.current.position);
      Object.assign(jobCache.current, Object.fromEntries(loaded.map(job => [job.id, job])));
      adopt(next); setVideo(asset); setWindows(old => [...history, ...old.filter(w => w.cursor < (history.at(-1)?.cursor ?? 0) && !history.some(item => item.id === w.id))]); setJobs(old => ({ ...old, ...Object.fromEntries(loaded.map(job => [job.id, job])) }));
      setConnected(true); setUpdated(Date.now()); if (!retry.current) setError(null);
      if (hydrated.current !== next.segment_id) {
        hydrated.current = next.segment_id;
        setFrames(next.configuration.preprocessing.frames); setFps(next.configuration.preprocessing.fps); setSize(next.configuration.preprocessing.resize);
        setSettings({ model: next.configuration.model, prompt_text: next.configuration.prompt_text, generation: { ...next.configuration.generation } });
      }
      if (initialPosition.current) {
        syntheticPosition.current = next.position; setPosition(next.position);
        // Reload cannot restore browser playback. Explicitly pause persisted admission.
        initialPosition.current = false;
        if (next.state === "running") void send({ action: "pause" }, false, true);
      }
    } catch (cause) {
      if (token === fence.current) { setConnected(false); setError(message(cause)); media.current?.pause(); initialPosition.current = true; }
    } finally { polling.current = false; }
  }

  useEffect(() => {
    const token = ++fence.current;
    void poll(token);
    const timer = window.setInterval(() => { setNow(Date.now()); void poll(fence.current); }, 1000);
    return () => { clearInterval(timer); fence.current++; media.current?.pause(); const leaving = current.current; if (leaving?.id === id && leaving.state === "running") void commandSession(leaving.id, { action: "pause", command_id: crypto.randomUUID() }).catch(() => {}); };
  }, [id]); // Each selection invalidates every previous asynchronous response.

  async function send(command: MonitoringCommand, isRetry = false, preserveRetry = false) {
    const saved = current.current;
    if (!saved) return;
    while (positionPending.current) await new Promise(resolve => window.setTimeout(resolve, 20));
    if (current.current?.id !== saved.id) return;
    if (pending.current) return;
    const payload = isRetry && retry.current ? retry.current.command : { ...command, command_id: crypto.randomUUID() };
    const target = isRetry && retry.current ? retry.current.id : saved.id;
    const token = payload.action === "position" ? fence.current : ++fence.current;
    positionPending.current = payload.action === "position";
    pending.current = true; if (!positionPending.current) setBusy(true); if (!preserveRetry) setError(null);
    if (!preserveRetry) retry.current = { id: target, command: payload };
    try {
      const next = await commandSession(target, payload);
      if (token !== fence.current) {
        if (["start", "resume", "restart"].includes(payload.action) && next.state === "running") void commandSession(target, { action: "pause", command_id: crypto.randomUUID() }).catch(() => {});
        return;
      }
      if (payload.action === "position" && (current.current?.generation !== next.generation || current.current.segment_id !== next.segment_id)) return;
      if (!preserveRetry) retry.current = null; current.current = next; adopt(next); setConnected(true);
      if (payload.action !== "position") {
        syntheticPosition.current = next.position; setPosition(next.position);
        if (media.current && Math.abs(media.current.currentTime - next.position) > 0.1) media.current.currentTime = next.position;
      }
      if (["start", "resume", "restart"].includes(payload.action) && media.current) {
        try { await media.current.play(); } catch { if (token !== fence.current) return; setError("Playback was blocked. Press Resume to try again."); const paused = await commandSession(target, { action: "pause", command_id: crypto.randomUUID() }); if (token === fence.current) adopt(paused); }
      }
      return next;
    } catch (cause) { if (token === fence.current) { setError(message(cause)); setConnected(false); media.current?.pause(); initialPosition.current = true; } }
    finally { pending.current = false; positionPending.current = false; setBusy(false); }
  }

  useEffect(() => {
    let last = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const saved = current.current;
      if (saved?.state === "running" && connected && !pending.current && !initialPosition.current && !retry.current) {
        const actual = video?.source === "synthetic" ? Math.min(saved.duration, syntheticPosition.current + (now - last) / 1000) : media.current?.currentTime;
        if (actual !== undefined && Number.isFinite(actual)) {
          syntheticPosition.current = actual; setPosition(actual);
          if (actual >= saved.position) void send({ action: "position", position_seconds: Math.min(actual, saved.duration) });
        }
      }
      last = now;
    }, 500);
    return () => clearInterval(timer);
  }, [video?.source, connected]);

  const newestWindow = windows.find(window => window.generation === session?.generation && window.segment_id === session?.segment_id);
  const inferenceState = newestWindow?.job_id ? jobs[newestWindow.job_id]?.state ?? newestWindow.state : newestWindow?.state ?? "waiting for playback";
  const latest = session?.latest_job_id ? jobs[session.latest_job_id] : null;
  const settingsValid = settings && settings.prompt_text.trim() && Number.isInteger(settings.generation.max_tokens) && settings.prompt_text.length <= 16000 && settings.generation.max_tokens >= 16 && settings.generation.max_tokens <= 4096 && settings.generation.temperature >= 0 && settings.generation.temperature <= 2 && frames >= 2 && frames <= 32 && Number.isInteger(frames) && fps > 0 && fps <= 30 && (frames - 1) / fps <= 30;
  function configuration(videoId: string) { return { ...settings!, video_id: videoId, frame_count: frames, fps, size }; }
  async function create() {
    if (!source.video || !source.duration || !settingsValid) return;
    setBusy(true);
    try { const next = await createSession(configuration(source.video.id), source.duration); select(next.id); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  async function older() {
    if (!session || !windows.length) return;
    const token = fence.current;
    try {
      const page = await getWindows(session.id, windows[windows.length - 1].cursor);
      const loaded = await Promise.all(page.flatMap(w => w.job_id ? [getJob(w.job_id)] : []));
      if (token !== fence.current) return;
      Object.assign(jobCache.current, Object.fromEntries(loaded.map(job => [job.id, job])));
      setWindows(old => [...old, ...page.filter(w => !old.some(existing => existing.id === w.id))]);
      setJobs(old => ({ ...old, ...Object.fromEntries(loaded.map(job => [job.id, job])) }));
    } catch (cause) { setError(message(cause)); }
  }

  const duration = session?.duration ?? 0;
  const windowSeconds = session ? (session.configuration.preprocessing.frames - 1) / session.configuration.preprocessing.fps : (frames - 1) / fps;
  const stale = !connected || (updated > 0 && now - updated > 3000);
  const systemState = connected ? session?.state ?? "recovering" : "disconnected";
  return <section className="monitoring" aria-label="Recorded monitoring">
    <div className="intro"><div><h1>Recorded monitoring</h1><p>Replay a recording as if it were live. Each prediction describes the first part of its input window; completion time is processing metadata.</p></div><span className="mock-badge">{session?.configuration.backend_kind === "mock" || (!session && capabilities?.simulated) ? "Simulated predictions" : session || capabilities ? "Online vLLM" : "Backend unavailable"}</span></div>
    <div className="session-bar">
      <label className="session-picker"><span>Session</span><select aria-label="Saved monitoring session" value={id} onChange={event => select(event.target.value)}><option value="">New session</option>{sessions.map(item => <option key={item.id} value={item.id}>{sessionName(item)}</option>)}</select></label>
      {session && <p className="download-links"><a href={`/api/monitoring-sessions/${session.id}/export?format=json`} download>Download complete session JSON</a><a href={`/api/monitoring-sessions/${session.id}/export?format=csv`} download>Download complete session CSV</a></p>}
    </div>
    <div className="workspace">
      <div className="viewer-panel">
        {!id ? <><SourceSelection {...source} onSample={() => void source.useSample()} onBrowseDataset={() => void source.browseDataset()} onDatasetPathChange={source.setSelectedDatasetPath} onOpenDataset={() => void source.useDatasetVideo()} onUpload={file => void source.upload(file)} onDurationLoaded={source.onDurationLoaded} />{source.error && <p className="error-message" role="alert">{source.error}</p>}<button className="analyze-button" disabled={busy || !source.video || !source.duration || !settingsValid} onClick={() => void create()}>Create monitoring session</button></> : <>
          <div className="panel-heading"><h2>{video?.filename ?? "Recovering recording…"}</h2><p className="playback-clock">Playback {seconds(Math.min(position, duration))} / {seconds(duration)}</p></div>
          <div className="replay-screen">
            {video?.source !== "synthetic" && video && <video ref={media} src={`/api/videos/${video.id}/media`} onLoadedMetadata={event => { event.currentTarget.currentTime = current.current?.position ?? 0; }} onEnded={event => { const saved = current.current; void send({ action: "position", position_seconds: saved?.duration ?? event.currentTarget.currentTime }); }} onError={() => { setError("Recording playback unavailable"); void send({ action: "pause" }); }} />}
            {video?.source === "synthetic" && <p className="replay-placeholder">Synthetic recording · simulated playback clock</p>}
          </div>
          {session && <CoverageTimeline windows={windows.filter(window => window.generation === session.generation)} jobs={jobs} duration={duration} position={position} />}
          <label className="seek-control">Seek recording <input aria-label="Seek recording" type="range" min="0" max={duration} step="0.1" value={position} disabled={busy || !session || !connected} onChange={event => { media.current?.pause(); void send({ action: "seek", position_seconds: Number(event.target.value) }).then(next => { if (next && current.current?.id === next.id && current.current.generation === next.generation && current.current.segment_id === next.segment_id && current.current.state === "running") { const playbackFence = fence.current; void media.current?.play().catch(() => { if (playbackFence === fence.current) { setError("Playback was blocked. Press Resume to try again."); void send({ action: "pause" }); } }); } }); }} /></label>
          <div className="replay-controls"><button className="button primary" disabled={busy || !session || !connected || session.state === "running" || session.state === "stopped"} onClick={() => void send({ action: session?.position === 0 ? "start" : "resume" })}>{session?.position === 0 ? "Start" : "Resume"}</button><button className="button secondary" disabled={busy || session?.state !== "running"} onClick={() => { media.current?.pause(); void send({ action: "pause" }); }}>Pause</button><button className="button secondary" disabled={busy || !session || session.state === "stopped"} onClick={() => { media.current?.pause(); void send({ action: "stop" }); }}>Stop</button><button className="button secondary restart" disabled={busy || !session || !connected} onClick={() => void send({ action: "restart", position_seconds: 0 })}>Restart / retry inference</button></div>
          <div className="replay-status">
            <p role="status" data-tone={toneOf(systemState)}>System: {systemState}{busy ? " · updating" : ""}{session?.recovery_reason ? ` · ${session.recovery_reason}` : ""}</p>
            <p role="status" data-tone={toneOf(inferenceState)}>Inference: {inferenceState}{newestWindow?.reason ? ` · ${newestWindow.reason}` : ""}</p>
            <p data-tone={stale ? "failed" : "idle"}>Result lag: {latest ? seconds(Math.max(0, position - latest.end_seconds)) : "no prediction yet"}{stale ? " · stale status" : ""}</p>
          </div>
          <p className="preview-note">Window {windowSeconds.toFixed(2)} s, stride {session?.stride} s, expiration {session?.expiration} s. Status is polled every second; coverage gaps and system failures are never reported as activities.</p>
        </>}
      </div>
      <aside className="analysis-panel">
        {error && <p className="error-message" role="alert">{error} <button onClick={() => retry.current ? void send(retry.current.command, true) : void poll(fence.current)}>Retry connection / command</button></p>}
        {id && <section className="monitor-latest"><h2>Latest activity</h2>{latest?.prediction ? <Result job={latest} /> : <div className="result-placeholder"><p>No completed prediction in the current generation and segment.</p></div>}</section>}
        <section className="monitor-config"><h2>Configuration</h2>
          <label className="setting-row"><span>Frames</span><input aria-label="Monitoring frames" type="number" value={frames} min="2" max="32" onChange={event => setFrames(Number(event.target.value))} /></label>
          <label className="setting-row"><span>Sampling FPS</span><input aria-label="Monitoring FPS" type="number" value={fps} min="0.1" max="30" step="0.1" onChange={event => setFps(Number(event.target.value))} /></label>
          <label className="setting-row"><span>Crop size</span><select aria-label="Monitoring crop" value={size} onChange={event => setSize(Number(event.target.value))}><option value={224}>224 × 224</option><option value={448}>448 × 448</option><option value={672}>672 × 672</option></select></label>
          {capabilities && settings && <ExperimentControls capabilities={capabilities} settings={settings} busy={busy} onChange={setSettings} />}
          {session && <><button className="button secondary wide" disabled={busy || !connected || !settingsValid} onClick={() => void send({ action: "configure", configuration: configuration(session.video_id) })}>Apply new configuration segment</button><p className="preview-note">Current generation {session.generation} · segment {session.segment_id.slice(0, 8)}</p><details className="plain-details"><summary>Saved segment settings</summary><pre className="saved-prompt">{JSON.stringify(session.configuration, null, 2)}</pre></details></>}
        </section>
        <details className="plain-details"><summary>All 16 activity labels</summary><p>{labels.join(", ")}</p></details>
      </aside>
    </div>
    {session && <section className="coverage-history"><h2>Coverage and prediction history</h2>{windows.length === 0 && <p className="preview-note">Windows appear here as playback passes them.</p>}<ul>{windows.map(window => { const state = window.job_id ? jobs[window.job_id]?.state ?? window.state : window.state; const job = window.job_id ? jobs[window.job_id] : undefined; return <li key={window.id}><details><summary><i data-tone={toneOf(state, job)} aria-hidden="true" /><span className="history-range">{seconds(window.start_seconds)}–{seconds(window.end_seconds)}</span><span className="history-meta">Generation {window.generation} · #{window.sequence}{window.sequence_end !== window.sequence ? `–${window.sequence_end}` : ""} · {state}{window.reason ? ` ${window.reason}` : ""}{session && (window.generation !== session.generation || window.segment_id !== session.segment_id) ? " · historical" : ""}</span>{job?.prediction && <strong className={isAlert(job) ? "alert" : ""}>{job.prediction.label.replaceAll("_", " ")}</strong>}</summary><p className="preview-note">Segment {window.segment_id.slice(0, 8)} · {window.reason ?? "No coverage gap recorded"}</p>{job && <Result job={job} />}</details></li>; })}</ul>{windows.length >= 100 && <button className="text-button" onClick={() => void older()}>Load older history</button>}</section>}
  </section>;
}

const shortId = (value: string | null) => !value ? "unknown" : value.length > 24 ? `${value.slice(0, 24)}…` : value;
const isAlert = (job: AnalysisJob) => ["fall", "fallen"].includes(job.prediction?.label ?? "");
function toneOf(state: string, job?: AnalysisJob) {
  if (state === "succeeded") return job && isAlert(job) ? "alert" : "done";
  if (["failed", "cancelled", "disconnected"].includes(state)) return "failed";
  if (["skipped", "stopped"].includes(state)) return "gap";
  if (["pending", "preparing", "submitted", "queued", "running"].includes(state)) return "active";
  return "idle";
}
function sessionName(item: MonitoringSession) {
  const created = item.created_at ? new Date(item.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : item.id.slice(0, 8);
  return `${created} · ${item.state} · ${item.id.slice(0, 8)}`;
}

const legend = [["done", "Other activity"], ["alert", "Fall or fallen"], ["active", "In progress"], ["gap", "Not covered"], ["failed", "Failed"]] as const;
function CoverageTimeline({ windows, jobs, duration, position }: { windows: MonitoringWindow[]; jobs: Record<string, AnalysisJob>; duration: number; position: number }) {
  if (!duration) return null;
  const pct = (value: number) => `${Math.min(100, Math.max(0, (value / duration) * 100))}%`;
  const tones = windows.map(window => { const job = window.job_id ? jobs[window.job_id] : undefined; return { window, job, tone: toneOf(job?.state ?? window.state, job) }; });
  const counts = Object.fromEntries(legend.map(([tone]) => [tone, tones.filter(item => item.tone === tone).length]));
  return <div className="coverage">
    <div className="coverage-bar" aria-hidden="true">
      {[...tones].reverse().map(({ window, job, tone }) => <i key={window.id} data-tone={tone} style={{ left: pct(window.start_seconds), width: pct(window.end_seconds - window.start_seconds) }} title={`${seconds(window.start_seconds)}–${seconds(window.end_seconds)} · ${job?.prediction?.label ?? job?.state ?? window.state}${window.reason ? ` (${window.reason})` : ""}`} />)}
      <b style={{ left: pct(position) }} />
    </div>
    <div className="window-track-scale"><span>0 s</span><span>{duration.toFixed(1)} s</span></div>
    <ul className="coverage-legend">{legend.filter(([tone]) => counts[tone] > 0).map(([tone, name]) => <li key={tone}><i data-tone={tone} aria-hidden="true" />{name} <span>{counts[tone]}</span></li>)}</ul>
  </div>;
}

function Result({ job }: { job: AnalysisJob }) {
  const prediction = job.prediction;
  const completed = prediction ? new Date(prediction.completed_at) : null;
  return <div className="result-card">
    <div className="result-overline"><span>System state: {job.state}{job.error ? ` · ${job.error}` : ""}</span>{prediction?.backend_kind === "mock" && <span>Simulated</span>}</div>
    {prediction && <>
      <strong className={`result-label ${isAlert(job) ? "alert" : ""}`}>{prediction.label.replaceAll("_", " ")}</strong>
      <p className="simulation-note">Describes the first part of the input window.{prediction.backend_kind === "mock" && <> Simulated by fixture <span title={prediction.fixture_version ?? undefined}>{shortId(prediction.fixture_version)}</span>.</>}</p>
      <dl>
        <div><dt>Input interval</dt><dd>{seconds(job.start_seconds)}–{seconds(job.end_seconds)}</dd></div>
        <div><dt>Request time</dt><dd>{prediction.request_duration_ms.toFixed(0)} ms</dd></div>
        <div><dt>Pipeline time</dt><dd>{prediction.total_duration_ms.toFixed(0)} ms</dd></div>
        <div><dt>Completed</dt><dd title={prediction.completed_at}>{completed && !Number.isNaN(completed.getTime()) ? completed.toLocaleTimeString() : prediction.completed_at}</dd></div>
      </dl>
      <p className="frame-times">Actual frame timestamps: {prediction.sampled_timestamps.map(seconds).join(", ")}</p>
      {prediction.backend_kind === "mock" && <p className="simulation-note">Local timings include the mock's injected delay; they are not GPU throughput measurements.</p>}
    </>}
    <details className="result-details"><summary>Immutable input and configuration</summary><p className="raw-response">Job {job.id} · configuration {job.configuration_id} · prepared input {job.prepared_input_id ?? "synthetic"}</p><pre className="saved-prompt">{JSON.stringify(job.configuration, null, 2)}</pre></details>
  </div>;
}
