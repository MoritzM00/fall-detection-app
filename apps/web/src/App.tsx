import { useEffect, useMemo, useRef, useState } from "react";
import { createDatasetVideo, createJob, createSample, getCapabilities, getVideo, listDatasetVideos, retryJob, uploadVideo } from "./api";
import type { DatasetVideoOption, VideoAsset } from "./api";
import { fpsForEnd, submittedEnd } from "./runState";
import { usePreparation } from "./usePreparation";
import { useRunHistory } from "./useRunHistory";
import { FramePreview } from "./FramePreview";

function formatLabel(label: string): string {
  return label.replaceAll("_", " ");
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

function sampleTimestamps(startSeconds: number, endSeconds: number, count = 16): number[] {
  const step = (endSeconds - startSeconds) / (count - 1);
  return Array.from({ length: count }, (_, index) => startSeconds + step * index);
}

export default function App() {
  const [video, setVideo] = useState<VideoAsset | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const { jobs, selectedJob: job, selectedJobId, activeJob, pollError, ready: historyReady, setSelectedJobId, refresh, record } = useRunHistory();
  const [jobVideo, setJobVideo] = useState<VideoAsset | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [startSeconds, setStartSeconds] = useState(0);
  const [frameCount, setFrameCount] = useState(16);
  const [fps, setFps] = useState(7.5);
  const endSeconds = submittedEnd(startSeconds, frameCount, fps);
  const [size, setSize] = useState(448);
  const [capabilities, setCapabilities] = useState<{ simulated: boolean; models: string[]; backend_kind: string } | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const simulated = capabilities?.simulated === true;
  const [datasetVideos, setDatasetVideos] = useState<DatasetVideoOption[]>([]);
  const [selectedDatasetPath, setSelectedDatasetPath] = useState("");
  const [showDatasetBrowser, setShowDatasetBrowser] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function selectSource(nextVideo: VideoAsset | null, url: string | null) {
    setVideo(nextVideo);
    setPreviewUrl(url);
    setDuration(nextVideo?.duration_seconds ?? null);
    setStartSeconds(0);
    setFrameCount(16);
    setFps(7.5);
    setSize(448);
    if (!nextVideo && fileInput.current) fileInput.current.value = "";
  }

  const previewTimestamps = useMemo(
    () => endSeconds > startSeconds ? sampleTimestamps(startSeconds, endSeconds, frameCount) : [],
    [startSeconds, endSeconds, frameCount],
  );
  const rangeDuration = endSeconds - startSeconds;
  const isBaselineWindow = Math.abs(rangeDuration - 2) < 0.001;
  const rangeValid = startSeconds >= 0 && endSeconds > startSeconds && rangeDuration <= 30 && fps > 0 && fps <= 30 && frameCount >= 2 && frameCount <= 32 && (duration === null || endSeconds <= duration + 0.01);
  const { visiblePrepared, preparing, preparationError } = usePreparation(video, startSeconds, frameCount, fps, size, rangeValid);

  useEffect(() => {
    getCapabilities().then(setCapabilities).catch((cause) => setCapabilityError(cause instanceof Error ? cause.message : "Could not load capabilities"));
  }, []);

  useEffect(() => {
    if (!job) { setJobVideo(null); return; }
    setJobVideo(null);
    let cancelled = false;
    getVideo(job.video_id).then((asset) => { if (!cancelled) setJobVideo(asset); })
      .catch(() => { if (!cancelled) setJobVideo(null); });
    return () => { cancelled = true; };
  }, [job?.video_id]);

  useEffect(() => {
    return () => {
      if (previewUrl?.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  async function useSample() {
    setBusy(true);
    setError(null);
    try {
      const sample = await createSample();
      selectSource(sample, null);
    } catch (sampleError) {
      setError(sampleError instanceof Error ? sampleError.message : "Could not create sample");
    } finally {
      setBusy(false);
    }
  }

  async function onFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const uploaded = await uploadVideo(file);
      selectSource(uploaded, URL.createObjectURL(file));
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function browseDataset() {
    setError(null);
    setShowDatasetBrowser(true);
    if (datasetVideos.length > 0) return;
    setBusy(true);
    try {
      const options = await listDatasetVideos();
      setDatasetVideos(options);
      setSelectedDatasetPath(options[0]?.path ?? "");
      if (options.length === 0) setError("No prepared dataset videos were found");
    } catch (datasetError) {
      setError(datasetError instanceof Error ? datasetError.message : "Could not read the dataset catalog");
    } finally {
      setBusy(false);
    }
  }

  async function useDatasetVideo() {
    if (!selectedDatasetPath) return;
    setBusy(true);
    setError(null);
    try {
      const asset = await createDatasetVideo(selectedDatasetPath);
      selectSource(asset, `/api/videos/${asset.id}/media`);
      setShowDatasetBrowser(false);
    } catch (datasetError) {
      setError(datasetError instanceof Error ? datasetError.message : "Could not open the dataset video");
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    if (!video || activeJob || !historyReady) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createJob(video.id, startSeconds, endSeconds, visiblePrepared?.id ?? null, frameCount, fps, size);
      record(created);
    } catch (jobError) {
      setError(jobError instanceof Error ? jobError.message : "Could not start analysis");
    } finally {
      setBusy(false);
    }
  }

  const active = Boolean(activeJob);
  const result = job?.prediction;
  function updateRange(nextStart: number, nextEnd: number) {
    if (busy) return;
    setStartSeconds(nextStart);
    const nextFps = fpsForEnd(nextStart, nextEnd, frameCount);
    if (nextFps !== null) setFps(nextFps);
    setError(null);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Sentinel home">
          <span className="brand-mark"><i /><i /><i /></span>
          <span>Sentinel</span>
        </a>
        <div className="mode-tabs" aria-label="Application mode">
          <button className="active">Clip analysis</button>
          <button disabled>Monitoring <small>soon</small></button>
        </div>
        <span className="system-state"><i /> {capabilities ? simulated ? "Local simulation" : "Online vLLM" : "Backend unavailable"}</span>
      </header>

      <section className="intro" id="top">
        <div>
          <span className="eyebrow">Activity intelligence · prototype 01</span>
          <h1>See the moment.<br /><em>Understand the motion.</em></h1>
        </div>
        <p>Choose a clip, set a time window, inspect the frames, and run activity analysis.</p>
      </section>

      <section className="workspace">
        <div className="viewer-panel">
          <div className="panel-heading">
            <div><span>01</span><h2>Source</h2></div>
            {video && <span className="asset-pill">{video.source === "synthetic" ? "Synthetic" : video.source === "dataset" ? "OmniFall" : "Uploaded"}</span>}
          </div>

          {video && <div className="source-caption"><strong>{video.filename}</strong><span>{duration !== null ? `${duration.toFixed(1)} s clip` : "Loading duration…"}</span></div>}

          <div className={`viewer ${video ? "loaded" : ""}`}>
            {!video && (
              <div className="empty-state">
                <span className="upload-glyph">↗</span>
                <h3>Choose a clip</h3>
                <p>Use the built-in scenario now, or bring an MP4, MOV, WebM, or MKV.</p>
                <div className="source-actions">
                  <button className="button primary" onClick={useSample} disabled={busy}>Use sample clip</button>
                  <button className="button secondary" onClick={browseDataset} disabled={busy}>Browse dataset</button>
                  <button className="button secondary" onClick={() => fileInput.current?.click()} disabled={busy}>Upload video</button>
                </div>
                {showDatasetBrowser && (
                  <div className="dataset-browser">
                    <label htmlFor="dataset-video">Prepared OmniFall video</label>
                    <div>
                      <select id="dataset-video" value={selectedDatasetPath} onChange={(event) => setSelectedDatasetPath(event.target.value)} disabled={busy || datasetVideos.length === 0}>
                        {datasetVideos.map((option) => (
                          <option value={option.path} key={option.path}>{option.collection} · {option.subject} · {option.filename}</option>
                        ))}
                      </select>
                      <button className="button primary" onClick={useDatasetVideo} disabled={busy || !selectedDatasetPath}>Open clip</button>
                    </div>
                    <small>{datasetVideos.length > 0 ? `${datasetVideos.length} local videos · folder names are dataset groupings, not predictions` : busy ? "Reading local catalog…" : "No videos available. Try a sample or upload a clip."}</small>
                  </div>
                )}
              </div>
            )}
            {video?.source === "synthetic" && (
              <div className="synthetic-scene" aria-label="Animated synthetic corridor preview">
                <div className="window-light" />
                <div className="corridor-line left" /><div className="corridor-line right" />
                <div className="figure"><span className="head" /><span className="body" /><span className="leg one" /><span className="leg two" /></div>
                <span className="scene-label">SYNTHETIC CORRIDOR · 00:06</span>
              </div>
            )}
            {video && video.source !== "synthetic" && previewUrl && (
              <video
                src={previewUrl}
                controls
                onLoadedMetadata={(event) => {
                  const nextDuration = event.currentTarget.duration;
                  if (!Number.isFinite(nextDuration)) return;
                  setDuration(nextDuration);
                  if (nextDuration < endSeconds && nextDuration > startSeconds) {
                    setFps((frameCount - 1) / (nextDuration - startSeconds));
                  }
                }}
              />
            )}
          </div>

          {video && (
            <>
              <div className="clip-controls">
                <div><span className="control-label">Analysis window</span><strong>{formatTime(startSeconds)} — {formatTime(endSeconds)}</strong></div>
                <div className="range-fields">
                  <label>Start (s) <input disabled={busy} aria-invalid={!rangeValid} type="number" min="0" step="0.1" value={startSeconds} onChange={(event) => { if (Number.isFinite(event.currentTarget.valueAsNumber)) { const next = event.currentTarget.valueAsNumber; updateRange(next, next + (frameCount - 1) / fps); } }} /></label>
                  <span>to</span>
                  <label>End (s) <input disabled={busy} aria-invalid={!rangeValid} type="number" min={startSeconds + 0.1} max={duration ?? 30} step="0.1" value={endSeconds} onChange={(event) => { if (Number.isFinite(event.currentTarget.valueAsNumber)) updateRange(startSeconds, event.currentTarget.valueAsNumber); }} /></label>
                </div>
                <button className="text-button" disabled={busy} onClick={() => selectSource(null, null)}>Change source</button>
              </div>
              {!rangeValid && <p className="range-error" role="alert">Choose a valid window of up to 30 seconds within the clip.</p>}

              <FramePreview video={video} frameCount={frameCount} timestamps={previewTimestamps} prepared={visiblePrepared} preparing={preparing} error={preparationError} valid={rangeValid} />
            </>
          )}
        </div>

        <aside className="analysis-panel">
          <div className="panel-heading">
            <div><span>02</span><h2>Analysis</h2></div>
            <span className="mock-badge">{capabilities ? simulated ? "Simulated" : "Online vLLM" : "Unavailable"}</span>
          </div>

          <div className="setting-row"><span>Model</span><strong>{capabilities?.models[0] ?? "Unavailable"}</strong></div>
          <div className="setting-row"><label htmlFor="frame-count">Frames</label><input id="frame-count" type="number" min="2" max="32" step="1" value={frameCount} disabled={busy} onChange={(event) => { const next = event.currentTarget.valueAsNumber; if (Number.isInteger(next) && next >= 2 && next <= 32) setFrameCount(next); }} /></div>
          <div className="setting-row"><label htmlFor="target-fps">Sampling FPS</label><input id="target-fps" type="number" min="0.1" max="30" step="0.1" value={fps} disabled={busy} onChange={(event) => { const next = event.currentTarget.valueAsNumber; if (Number.isFinite(next) && next > 0 && next <= 30) setFps(next); }} /></div>
          <div className="setting-row"><label htmlFor="frame-size">Crop size</label><select id="frame-size" value={size} disabled={busy} onChange={(event) => setSize(Number(event.currentTarget.value))}><option value={224}>224 × 224</option><option value={336}>336 × 336</option><option value={448}>448 × 448</option><option value={672}>672 × 672</option></select></div>
          <div className="setting-row"><span>Preset</span><strong>Thesis baseline v1</strong></div>
          <div className="setting-row"><span>Window</span><strong>{rangeDuration.toFixed(2)} s {(!isBaselineWindow || fps !== 7.5 || frameCount !== 16 || size !== 448) && <small className="experimental">Experimental</small>}</strong></div>
          <p className="preview-note">Frame count and FPS set the window length. Editing the end time recalculates FPS.</p>

          <button className="analyze-button" disabled={!historyReady || !capabilities || !video || !rangeValid || busy || active || (video.source !== "synthetic" && (!visiblePrepared || preparing)) || (video.source === "synthetic" && !simulated)} onClick={analyze}>
            {active ? <><i className="spinner" /> {activeJob?.state === "queued" ? "Queued" : "Analyzing"}</> : busy ? "Please wait…" : result ? "Run again" : "Run analysis"}
          </button>

          {error && <p className="error-message" role="alert">{error}</p>}
          {capabilityError && <p className="error-message" role="alert">Backend settings unavailable: {capabilityError}</p>}
          {!historyReady && !pollError && <p className="job-status" role="status">Recovering recent runs…</p>}
          {pollError && <p className="error-message" role="alert">Run status unavailable: {pollError} <button onClick={() => void refresh()}>Retry</button></p>}

          {jobs.length > 0 && <section className="recent-runs" aria-label="Recent runs">
            <label htmlFor="recent-run">Recent runs</label>
            <select id="recent-run" value={selectedJobId ?? ""} onChange={(event) => setSelectedJobId(event.target.value)}>
              {jobs.map((item) => <option key={item.id} value={item.id}>{item.created_at.slice(0, 19)} · {item.state} · {item.video_id}</option>)}
            </select>
          </section>}
          {job && <p className="preview-note">Submitted source: {jobVideo?.id === job.video_id ? jobVideo.filename : job.video_id} · {job.start_seconds.toFixed(3)}–{job.end_seconds.toFixed(3)} s · {job.configuration?.model ?? "Saved model unavailable"}</p>}

          {!job && <div className="result-placeholder"><span>{video ? "Ready when you are" : "Start with a clip"}</span><p>{video?.source === "synthetic" && !simulated ? "Choose a real video for online vLLM analysis." : video ? simulated ? "Check your window, then run a simulated analysis." : "Check your prepared frames, then run analysis." : "Choose a sample, browse the dataset, or upload a video to get started."}</p></div>}
          {activeJob && <p className="job-status" role="status">{activeJob.state === "queued" ? "A submitted clip is queued for analysis." : activeJob.configuration?.backend_kind === "mock" ? "Generating a simulated result…" : "Analyzing prepared frames…"}</p>}

          {job?.state === "failed" && <div className="result-card failure" role="alert"><span>Processing failed</span><p>{job.error}</p><button className="button secondary" onClick={() => retryJob(job.id).then(record).catch((cause) => setError(String(cause)))} disabled={Boolean(activeJob)}>Retry run</button></div>}

          {result && (
            <div className="result-card" aria-live="polite">
              <div className="result-overline"><span>Activity result</span><span>{result.backend_kind === "mock" ? "SIMULATED" : result.backend_kind}</span></div>
              <div className={`result-label ${result.label === "fall" || result.label === "fallen" ? "alert" : ""}`}>{formatLabel(result.label)}</div>
              {result.backend_kind === "mock" && <p className="simulation-note">Demo output · this label is simulated, not inferred from your clip.</p>}
              <dl>
                <div><dt>Input window</dt><dd>{job.start_seconds.toFixed(3)}–{job.end_seconds.toFixed(3)} s</dd></div>
                <div><dt>Request time</dt><dd>{result.request_duration_ms.toFixed(0)} ms</dd></div>
              </dl>
              <details className="result-details">
                <summary>Run details</summary>
                <p className="raw-response">“{result.raw_response}”</p>
                <dl>
                <div><dt>Model</dt><dd>{result.model}</dd></div>
                <div><dt>Fixture</dt><dd>{result.fixture_version}</dd></div>
                <div><dt>Configuration</dt><dd title={job.configuration_id}>{job.configuration_id}</dd></div>
                {job.prepared_input_id && <div><dt>Prepared input</dt><dd title={job.prepared_input_id}>{job.prepared_input_id}</dd></div>}
                <div><dt>Sample times</dt><dd title={result.sampled_timestamps.join(", ")}>{result.sampled_timestamps.map((timestamp) => timestamp.toFixed(3)).join(", ")} s</dd></div>
                <div><dt>Frames</dt><dd>{result.sampled_timestamps.length}</dd></div>
                </dl>
              </details>
            </div>
          )}
        </aside>
      </section>

      <footer><span>{capabilities ? simulated ? "Local MVP · no model inference is occurring" : "Online vLLM analysis" : "Backend settings unavailable"}</span><span>Every result keeps its input and configuration identity</span></footer>
      <input ref={fileInput} type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska" hidden onChange={onFileChange} />
    </main>
  );
}
