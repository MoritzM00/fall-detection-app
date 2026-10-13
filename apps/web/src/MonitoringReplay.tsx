import { useEffect, useRef, useState } from "react";
import { createLiveSession, createSession } from "./api";
import type { Capabilities, ExperimentSettings, MonitoringSession } from "./api";
import { SourceSelection } from "./SourceSelection";
import { ExperimentSettingsGroup, SamplingSettingsGroup } from "./ExperimentControls";
import { baselineExperiment, experimentFromPreset, experimentValid, matchingPreset, presetFor, presetTitle, sameExperiment } from "./experimentState";
import { BASELINE_SAMPLING, SettingsDialog, SettingsGroup, SettingsSummary, isBaselineSampling, settingsSummary } from "./SettingsDialog";
import { useSourceSelection } from "./useSourceSelection";
import { useLiveCapture } from "./useLiveCapture";
import { contextBudget, contextWindowError } from "./context";
import { message, useMonitoringPoll } from "./useMonitoringPoll";
import { useSessionCommands } from "./useSessionCommands";
import { ActivityLabels, CoverageTimeline, History, LatestResult, ReplayControls, seconds, sessionName, toneOf } from "./MonitoringViews";

const LIVE_CAPTURE_FPS = 15;
const LIVE_SPAN_SECONDS = 120;

/** Editable experiment settings of a saved segment; older segments never reasoned. */
function savedExperiment(configuration: MonitoringSession["configuration"]): ExperimentSettings {
  return { model: configuration.model, prompt_text: configuration.prompt_text, chain_of_thought: configuration.chain_of_thought ?? false, generation: { ...configuration.generation } };
}

export function MonitoringReplay({ capabilities }: { capabilities: Capabilities | null }) {
  const source = useSourceSelection(() => {}, () => {});
  const [settings, setSettings] = useState<ExperimentSettings | null>(null);
  const [frames, setFrames] = useState(16);
  const [fps, setFps] = useState(7.5);
  const [size, setSize] = useState(448);
  const [sourceMode, setSourceMode] = useState<"recording" | "camera">("recording");
  const [settingsOpen, setSettingsOpen] = useState(false);
  function hydrate(next: MonitoringSession) {
    setFrames(next.configuration.preprocessing.frames); setFps(next.configuration.preprocessing.fps); setSize(next.configuration.preprocessing.resize);
    setSettings(savedExperiment(next.configuration));
  }
  // `commands` is assigned below, before any poll response can arrive.
  const poll = useMonitoringPoll({ hydrate, recover: next => commands.recover(next) });
  const commands = useSessionCommands(poll);
  const { id, select, session, video, windows, jobs, error, connected, media, current, retry } = poll;
  const { busy, setBusy, position, send } = commands;

  useEffect(() => { if (capabilities && !current.current) setSettings(baselineExperiment(capabilities)); }, [capabilities]);

  const live = video?.source === "live" || session?.source_kind === "live";
  const captureFps = session?.capture_fps ?? LIVE_CAPTURE_FPS;
  const capture = useLiveCapture(live && session ? session.id : null, live && session?.state === "running" && connected, captureFps, poll.refresh);
  const wantsCamera = live || (sourceMode === "camera" && (!id || !session));
  useEffect(() => { if (!wantsCamera) capture.disconnect(); }, [wantsCamera]); // Release the camera when no view needs it.
  async function begin(action: "start" | "resume" | "restart") {
    // Live sessions need frames as soon as they run; connect before the command.
    if (live && !(await capture.connect())) return;
    void send(action === "restart" ? { action, ...(live ? {} : { position_seconds: 0 }) } : { action });
  }

  const newestWindow = windows.find(window => window.generation === session?.generation && window.segment_id === session?.segment_id);
  const inferenceState = newestWindow?.job_id ? jobs[newestWindow.job_id]?.state ?? newestWindow.state : newestWindow?.state ?? "waiting for playback";
  const latest = session?.latest_job_id ? jobs[session.latest_job_id] : null;
  const minTokens = capabilities?.generation_limits.min_max_tokens ?? 16;
  const maxTokens = capabilities?.generation_limits.max_max_tokens ?? 4096;
  const maxFps = live || (!id && sourceMode === "camera") ? captureFps : 30;
  const settingsValid = settings !== null && experimentValid(settings, capabilities) && frames >= 2 && frames <= 32 && Number.isInteger(frames) && fps > 0 && fps <= maxFps && (frames - 1) / fps <= 30;
  const contextError = settingsValid ? contextWindowError(capabilities, frames, size, settings.prompt_text, settings.generation.max_tokens) : null;
  const configValid = settingsValid && !contextError;
  function configuration(videoId: string) { return { ...settings!, video_id: videoId, frame_count: frames, fps, size }; }
  async function create() {
    if (!source.video || !source.duration || !configValid) return;
    setBusy(true);
    try { const next = await createSession(configuration(source.video.id), source.duration); select(next.id); }
    catch (cause) { poll.setError(message(cause)); }
    finally { setBusy(false); }
  }
  async function createLive() {
    if (!configValid) return;
    setBusy(true);
    try { const next = await createLiveSession({ ...settings!, frame_count: frames, fps, size, capture_fps: LIVE_CAPTURE_FPS }); select(next.id); }
    catch (cause) { poll.setError(message(cause)); }
    finally { setBusy(false); }
  }

  // The summary describes the running segment when a session exists, else the pending form.
  const active = session?.configuration;
  const summarySampling = active ? [active.preprocessing.frames, active.preprocessing.fps, active.preprocessing.resize] as const : [frames, fps, size] as const;
  const summarySettings = active ? savedExperiment(active) : settings;
  const preset = capabilities && summarySettings ? matchingPreset(capabilities, summarySettings) : null;
  const baseline = isBaselineSampling(...summarySampling) && preset !== null;
  // Applying identical settings would only split the session into a needless segment.
  const unchanged = active !== undefined && settings !== null && frames === active.preprocessing.frames && fps === active.preprocessing.fps && size === active.preprocessing.resize
    && sameExperiment(settings, savedExperiment(active));
  function closeSettings(discard: boolean) {
    setSettingsOpen(false);
    if (!discard) return;
    // Unapplied edits would otherwise linger and misdescribe the running segment.
    if (current.current) hydrate(current.current);
    // A failed apply waiting for retry would otherwise reapply the settings just discarded.
    if (retry.current?.command.action === "configure") { retry.current = null; poll.setError(null); }
  }
  function resetSettings() {
    setFrames(BASELINE_SAMPLING.frames); setFps(BASELINE_SAMPLING.fps); setSize(BASELINE_SAMPLING.size);
    // Keep the chosen strategy and model; restore that preset's prompt and sampling.
    if (capabilities && settings) setSettings(experimentFromPreset(capabilities, presetFor(capabilities, settings.chain_of_thought), settings.model));
  }

  const duration = session?.duration ?? 0;
  // Resuming a finished recording would restart the media element at 0 while the session stays at its end.
  const ended = Boolean(session && !live && session.position >= session.duration);
  const windowSeconds = session ? (session.configuration.preprocessing.frames - 1) / session.configuration.preprocessing.fps : (frames - 1) / fps;
  const stale = !connected || (poll.updated > 0 && poll.now - poll.updated > 3000);
  const systemState = connected ? session?.state ?? "recovering" : "disconnected";
  const seek = <input aria-label="Seek recording" type="range" min="0" max={duration} step="0.1" value={position} disabled={busy || !session || !connected} onChange={event => commands.seek(Number(event.target.value))} />;
  // Recordings show the media clock; a live session's position is the server watermark.
  const shown = live ? session?.position ?? 0 : position;
  // The watermark only moves while frames arrive, so a stalled upload or a stale poll would
  // freeze a live result's age; add the wall time since the watermark last moved.
  const watermark = useRef({ position: -1, at: 0 });
  if (live && session && session.position !== watermark.current.position) watermark.current = { position: session.position, at: poll.now };
  const liveEdge = live && session?.state === "running" ? shown + Math.max(0, poll.now - watermark.current.at) / 1000 : shown;
  const span = live ? { from: Math.max(0, shown - LIVE_SPAN_SECONDS + 10), to: Math.max(shown, LIVE_SPAN_SECONDS - 10) + 10 } : { from: 0, to: duration };
  const partial = windows.length >= 100 && !poll.historyComplete;
  const older = () => void poll.older();
  const cameraPreview = capture.stream ? <video className="live-feed" ref={capture.attach} autoPlay muted playsInline /> : <div className="replay-placeholder"><span>Camera off</span><button className="button primary" disabled={busy} onClick={() => void capture.connect()}>Connect camera</button></div>;
  const cameraStatus = !capture.stream ? "off" : session?.state !== "running" ? "connected" : capture.stats.fps === null ? "starting" : `${capture.stats.fps.toFixed(1)} fps · upload lag ${capture.stats.lagSeconds?.toFixed(1) ?? "–"} s`;
  const summary = <SettingsSummary preset={preset && baseline && capabilities && summarySettings ? presetTitle(capabilities, preset, summarySettings.model) : null} invalid={!session && settings !== null && !configValid} detail={summarySettings ? settingsSummary(...summarySampling, summarySettings.chain_of_thought) : "Backend settings unavailable"} disabled={!settings} onEdit={() => setSettingsOpen(true)} />;
  return <section className="monitoring" aria-label="Monitoring" data-generation={session?.generation}>
    <h1 className="sr-only">Monitoring</h1>
    <div className="session-bar">
      <label className="session-picker"><span>Session</span><select aria-label="Saved monitoring session" value={id} onChange={event => select(event.target.value)}><option value="">New session</option>{poll.sessions.map(item => <option key={item.id} value={item.id}>{sessionName(item)}</option>)}</select></label>
      {/* The top bar already says "Local simulation" unless an old mock session is opened against a real backend. */}
      {session?.configuration.backend_kind === "mock" && capabilities?.simulated !== true && <span className="mock-badge">Simulated predictions</span>}
      {live && <span className="mock-badge live-badge">Live camera</span>}
      {session && <p className="download-links"><span>Export</span><a aria-label="Download complete session JSON" href={`/api/monitoring-sessions/${session.id}/export?format=json`} download>JSON</a><a aria-label="Download complete session CSV" href={`/api/monitoring-sessions/${session.id}/export?format=csv`} download>CSV</a></p>}
    </div>
    <div className="workspace">
      <div className="viewer-panel">
        {!id ? <>
          <div className="source-mode" aria-label="Session source"><button aria-pressed={sourceMode === "recording"} onClick={() => setSourceMode("recording")}>Recording</button><button aria-pressed={sourceMode === "camera"} onClick={() => setSourceMode("camera")}>Camera</button></div>
          {sourceMode === "recording" ? <>
            <SourceSelection {...source} onSample={() => void source.useSample()} onBrowseDataset={() => void source.browseDataset()} onDatasetPathChange={source.setSelectedDatasetPath} onOpenDataset={() => void source.useDatasetVideo()} onUpload={file => void source.upload(file)} onDurationLoaded={source.onDurationLoaded} onChangeSource={source.clear} />
            {source.error && <p className="error-message" role="alert">{source.error}</p>}
          </> : <>
            <div className="replay-screen">{cameraPreview}</div>
            {capture.error && <p className="error-message" role="alert">{capture.error}</p>}
            <p className="preview-note">Frames are captured at {LIVE_CAPTURE_FPS} fps and kept in the local data directory until you delete them. Use test footage of yourself; do not record other people without their consent.</p>
          </>}
        </> : <>
          <div className="panel-heading"><h2>{video?.filename ?? "Recovering recording…"}</h2><p className="playback-clock">{live ? `Live · ${seconds(shown)} captured` : `Playback ${seconds(Math.min(position, duration))} / ${seconds(duration)}`}</p></div>
          <div className="replay-screen">
            {live && cameraPreview}
            {live && capture.stream && session?.state === "running" && <span className="live-indicator">Live</span>}
            {video && video.source !== "synthetic" && video.source !== "live" && <video ref={media} src={`/api/videos/${video.id}/media`} onLoadedMetadata={event => { event.currentTarget.currentTime = current.current?.position ?? 0; }} onEnded={event => { const saved = current.current; void send({ action: "position", position_seconds: saved?.duration ?? event.currentTarget.currentTime }); }} onError={() => { poll.setError("Recording playback unavailable"); void send({ action: "pause" }); }} />}
            {video?.source === "synthetic" && <div className="replay-placeholder"><strong>{seconds(Math.min(position, duration))}</strong><span>Synthetic recording · simulated playback clock</span></div>}
          </div>
          {session && <CoverageTimeline windows={windows.filter(window => window.generation === session.generation)} jobs={jobs} from={span.from} to={span.to} position={shown} partial={partial} onLoadOlder={older} seek={live ? null : seek} />}
          <ReplayControls session={session} busy={busy} connected={connected} ended={ended} onBegin={action => void begin(action)} onPause={() => { media.current?.pause(); void send({ action: "pause" }); }} onStop={() => { media.current?.pause(); void send({ action: "stop" }); }} />
          <div className="replay-status">
            <p role="status" data-tone={stale ? "failed" : toneOf(systemState)}>System: {systemState}{busy ? " · updating" : ""}{session?.recovery_reason ? ` · ${session.recovery_reason}` : ""}{stale && connected ? " · stale status" : ""}</p>
            <p role="status" data-tone={toneOf(inferenceState)}>Inference: {inferenceState}{newestWindow?.reason ? ` · ${newestWindow.reason}` : ""}</p>
            {live && <p data-tone={!capture.stream ? "failed" : session?.state === "running" ? "done" : "idle"}>Camera: {cameraStatus}{capture.stats.queued > captureFps ? ` · ${capture.stats.queued} frames queued` : ""}</p>}
          </div>
          {session && <History windows={windows} jobs={jobs} session={session} more={partial} onLoadOlder={older} />}
        </>}
      </div>
      <aside className="analysis-panel">
        {id && capture.error && <p className="error-message" role="alert">{capture.error}</p>}
        {error && <p className="error-message" role="alert">{error} <button onClick={() => retry.current ? void send(retry.current.command, true) : poll.refresh()}>Retry connection / command</button></p>}
        {id ? <>
          {session ? <LatestResult job={latest ?? null} session={session} position={liveEdge} live={live} windows={windows} jobs={jobs} /> : <section className="monitor-latest"><h2>Last analyzed</h2><div className="result-placeholder"><p>No prediction yet.</p></div></section>}
          {summary}
        </> : <>
          {summary}
          {sourceMode === "recording"
            ? <button className="analyze-button" disabled={busy || !source.video || !source.duration || !configValid} onClick={() => void create()}>Create monitoring session</button>
            : <button className="analyze-button" disabled={busy || !capture.stream || !configValid} onClick={() => void createLive()}>Create live session</button>}
          <p className="preview-note">Each prediction describes the first part of its input window. Coverage gaps and failures are shown separately and never reported as an activity.</p>
        </>}
      </aside>
    </div>
    <SettingsDialog open={settingsOpen} title={session ? "Session settings" : "Monitoring settings"} onClose={() => closeSettings(true)} footer={<>
      <button type="button" className="text-button" disabled={busy} onClick={resetSettings}>Reset to research defaults</button>
      {session
        ? <button type="button" className="button primary" disabled={busy || !connected || !configValid || unchanged} onClick={() => { closeSettings(false); void send({ action: "configure", configuration: configuration(session.video_id) }); }}>Apply new configuration segment</button>
        : <button type="button" className="button primary" onClick={() => closeSettings(false)}>Done</button>}
    </>}>
      {session && <p className="preview-note">Changes apply from now on as a new configuration segment. Earlier predictions keep the settings they were made with.</p>}
      <SettingsGroup index="01" title="Preprocessing" hint="Which frames each window sends, and how." edited={!isBaselineSampling(frames, fps, size)}>
        <label className="setting-row"><span>Frames</span><input aria-label="Monitoring frames" type="number" value={frames} min="2" max="32" onChange={event => setFrames(Number(event.target.value))} /></label>
        {(live || (!id && sourceMode === "camera")) && <div className="setting-row"><span>Capture FPS</span><strong>{captureFps}</strong></div>}
        <label className="setting-row"><span>Sampling FPS</span><input aria-label="Monitoring FPS" type="number" value={fps} min="0.1" max={maxFps} step="0.1" onChange={event => setFps(Number(event.target.value))} /></label>
        <label className="setting-row"><span>Crop size</span><select aria-label="Monitoring crop" value={size} onChange={event => setSize(Number(event.target.value))}><option value={224}>224 × 224</option><option value={448}>448 × 448</option><option value={672}>672 × 672</option></select></label>
        <div className="setting-row"><span>Window length</span><strong>{((frames - 1) / fps).toFixed(2)} s</strong></div>
      </SettingsGroup>
      {capabilities && settings && <>
        <ExperimentSettingsGroup capabilities={capabilities} settings={settings} busy={busy} onChange={setSettings} />
        <SamplingSettingsGroup capabilities={capabilities} settings={settings} busy={busy} onChange={setSettings}
          budget={contextBudget(capabilities, frames, size, settings.prompt_text, settings.generation.max_tokens)} contextError={contextError} />
      </>}
      {settings && !settingsValid && <p className="error-message" role="alert">Use 2–32 frames, a sampling FPS above 0 and up to {maxFps}, a window of at most 30 s, a nonblank prompt, temperature from 0 to 2, top-p above 0 and up to 1, top-k of −1 or 1–1000, a non-negative integer seed, and an integer token limit from {minTokens} to {maxTokens}.</p>}
      {session && <section className="settings-section">
        <h3>Running segment</h3>
        <div className="setting-row"><span>Generation · segment</span><strong>{session.generation} · {session.segment_id.slice(0, 8)}</strong></div>
        <div className="setting-row"><span>Window · stride · expiration</span><strong>{windowSeconds.toFixed(2)} s · {session.stride} s · {session.expiration} s</strong></div>
        <details className="plain-details"><summary>Saved segment settings</summary><pre className="saved-prompt">{JSON.stringify(session.configuration, null, 2)}</pre></details>
      </section>}
      {capabilities && <ActivityLabels labels={capabilities.labels} />}
    </SettingsDialog>
  </section>;
}
