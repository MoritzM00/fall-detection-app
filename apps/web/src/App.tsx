import { useEffect, useMemo, useRef, useState } from "react";
import { createDatasetVideo, createJob, createPreparedInput, createSample, getCapabilities, getJob, listDatasetVideos, uploadVideo } from "./api";
import type { AnalysisJob, DatasetVideoOption, PreparedInput, VideoAsset } from "./api";

const terminalStates = new Set(["succeeded", "failed", "cancelled", "skipped"]);

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
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [startSeconds, setStartSeconds] = useState(0);
  const [endSeconds, setEndSeconds] = useState(2);
  const [frameCount, setFrameCount] = useState(16);
  const [fps, setFps] = useState(7.5);
  const [size, setSize] = useState(448);
  const [prepared, setPrepared] = useState<PreparedInput | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [preparationError, setPreparationError] = useState<string | null>(null);
  const [simulated, setSimulated] = useState(true);
  const [datasetVideos, setDatasetVideos] = useState<DatasetVideoOption[]>([]);
  const [selectedDatasetPath, setSelectedDatasetPath] = useState("");
  const [showDatasetBrowser, setShowDatasetBrowser] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const previewTimestamps = useMemo(
    () => endSeconds > startSeconds ? sampleTimestamps(startSeconds, endSeconds, frameCount) : [],
    [startSeconds, endSeconds, frameCount],
  );
  const rangeDuration = endSeconds - startSeconds;
  const isBaselineWindow = Math.abs(rangeDuration - 2) < 0.001;
  const rangeValid = startSeconds >= 0 && endSeconds > startSeconds && rangeDuration <= 30 && fps > 0 && fps <= 30 && frameCount >= 2 && frameCount <= 32 && (duration === null || endSeconds <= duration + 0.01);
  const preparedMatches = Boolean(prepared && video && prepared.video_id === video.id &&
    Math.abs(prepared.start_seconds - startSeconds) < 0.001 &&
    Math.abs(prepared.end_seconds - endSeconds) < 0.001 &&
    prepared.frame_count === frameCount && Math.abs(prepared.fps - fps) < 0.000001 && prepared.size === size);
  const visiblePrepared = preparedMatches ? prepared : null;

  useEffect(() => {
    getCapabilities().then((capabilities) => setSimulated(capabilities.simulated)).catch(() => undefined);
  }, []);

  useEffect(() => {
    setPrepared(null);
    setPreparationError(null);
    if (!video || video.source === "synthetic" || !rangeValid) {
      setPreparing(false);
      return;
    }
    let cancelled = false;
    setPreparing(true);
    const timer = window.setTimeout(async () => {
      try {
        const next = await createPreparedInput(video.id, startSeconds, frameCount, fps, size);
        if (!cancelled) setPrepared(next);
      } catch (prepareError) {
        if (!cancelled) setPreparationError(prepareError instanceof Error ? prepareError.message : "Frame preparation failed");
      } finally {
        if (!cancelled) setPreparing(false);
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [video, startSeconds, endSeconds, frameCount, fps, size, rangeValid]);

  useEffect(() => {
    return () => {
      if (previewUrl?.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  useEffect(() => {
    if (!job || terminalStates.has(job.state)) return;
    let cancelled = false;
    let pending = false;
    const timer = window.setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await getJob(job.id);
        if (!cancelled) setJob((current) => current?.id === next.id ? next : current);
      } catch (pollError) {
        if (!cancelled) setError(pollError instanceof Error ? pollError.message : "Could not read job status");
      } finally {
        pending = false;
      }
    }, 500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [job]);

  async function useSample() {
    setBusy(true);
    setError(null);
    setJob(null);
    try {
      const sample = await createSample();
      setVideo(sample);
      setPreviewUrl(null);
      setDuration(sample.duration_seconds ?? 2);
      setStartSeconds(0);
      setEndSeconds(2);
      setFrameCount(16); setFps(7.5); setSize(448);
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
    setJob(null);
    try {
      const uploaded = await uploadVideo(file);
      setVideo(uploaded);
      setPreviewUrl(URL.createObjectURL(file));
      setDuration(null);
      setStartSeconds(0);
      setEndSeconds(2);
      setFrameCount(16); setFps(7.5); setSize(448);
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
    setJob(null);
    try {
      const asset = await createDatasetVideo(selectedDatasetPath);
      setVideo(asset);
      setPreviewUrl(`/api/videos/${asset.id}/media`);
      setDuration(null);
      setStartSeconds(0);
      setEndSeconds(2);
      setFrameCount(16); setFps(7.5); setSize(448);
      setShowDatasetBrowser(false);
    } catch (datasetError) {
      setError(datasetError instanceof Error ? datasetError.message : "Could not open the dataset video");
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    if (!video) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await createJob(video.id, startSeconds, endSeconds, visiblePrepared?.id ?? null));
    } catch (jobError) {
      setError(jobError instanceof Error ? jobError.message : "Could not start analysis");
    } finally {
      setBusy(false);
    }
  }

  const active = job && !terminalStates.has(job.state);
  const result = job?.prediction;
  function updateRange(nextStart: number, nextEnd: number) {
    if (busy) return;
    setStartSeconds(nextStart);
    setEndSeconds(nextEnd);
    if (nextEnd > nextStart) setFps((frameCount - 1) / (nextEnd - nextStart));
    setJob(null);
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
        <span className="system-state"><i /> {simulated ? "Local simulation" : "Online vLLM"}</span>
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
                    setEndSeconds(nextDuration);
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
                <button className="text-button" disabled={busy} onClick={() => { setVideo(null); setJob(null); setPreviewUrl(null); setDuration(null); if (fileInput.current) fileInput.current.value = ""; }}>Change source</button>
              </div>
              {!rangeValid && <p className="range-error" role="alert">Choose a valid window of up to 30 seconds within the clip.</p>}

              <section className="frame-preview" aria-labelledby="frame-preview-title">
                <div className="frame-preview-heading">
                  <div><span className="control-label">Frames for analysis</span><strong id="frame-preview-title">{frameCount} timestamped frames</strong></div>
                  <span className="preview-disclaimer">{video.source === "synthetic" ? "Synthetic preview" : visiblePrepared ? "Frames sent to vLLM" : "Preparing frames…"}</span>
                </div>
                {preparing && <p className="preview-note" role="status">Decoding the selected video window…</p>}
                {preparationError && <p className="range-error" role="alert">{preparationError}</p>}
                {rangeValid && (video.source === "synthetic" || visiblePrepared) && <div className="frame-strip">
                  {(visiblePrepared ? visiblePrepared.frames.map((frame) => frame.actual_seconds) : previewTimestamps).map((timestamp, index) => (
                    <figure className="sample-frame" key={`${timestamp}-${index}`}>
                      <div className="frame-image">
                        {visiblePrepared ? (
                          <img src={`/api/prepared-inputs/${visiblePrepared.id}/frames/${index}`} alt={`Prepared frame ${index + 1}`} />
                        ) : (
                          <div className="synthetic-frame">
                            <i className="mini-window" />
                            <i className="mini-figure" style={{ transform: `rotate(${Math.min(78, (index / 15) * 88)}deg)` }} />
                          </div>
                        )}
                        <span>{String(index + 1).padStart(2, "0")}</span>
                      </div>
                      <figcaption>{timestamp.toFixed(3)}s</figcaption>
                    </figure>
                  ))}
                </div>}
                <p className="preview-note">{visiblePrepared ? "These JPEG previews are the encoded frames in the online video request. The activity label is simulated when using the mock backend." : "Change the window or settings to prepare a new frame bundle."}</p>
              </section>
            </>
          )}
        </div>

        <aside className="analysis-panel">
          <div className="panel-heading">
            <div><span>02</span><h2>Analysis</h2></div>
            <span className="mock-badge">{simulated ? "Simulated" : "Online vLLM"}</span>
          </div>

          <div className="setting-row"><span>Model</span><strong>Qwen3-VL 8B</strong></div>
          <div className="setting-row"><label htmlFor="frame-count">Frames</label><input id="frame-count" type="number" min="2" max="32" step="1" value={frameCount} disabled={busy} onChange={(event) => { const next = event.currentTarget.valueAsNumber; if (Number.isInteger(next) && next >= 2 && next <= 32) { setFrameCount(next); setEndSeconds(startSeconds + (next - 1) / fps); setJob(null); } }} /></div>
          <div className="setting-row"><label htmlFor="target-fps">Sampling FPS</label><input id="target-fps" type="number" min="0.1" max="30" step="0.1" value={fps} disabled={busy} onChange={(event) => { const next = event.currentTarget.valueAsNumber; if (Number.isFinite(next) && next > 0 && next <= 30) { setFps(next); setEndSeconds(startSeconds + (frameCount - 1) / next); setJob(null); } }} /></div>
          <div className="setting-row"><label htmlFor="frame-size">Crop size</label><select id="frame-size" value={size} disabled={busy} onChange={(event) => { setSize(Number(event.currentTarget.value)); setJob(null); }}><option value={224}>224 × 224</option><option value={336}>336 × 336</option><option value={448}>448 × 448</option><option value={672}>672 × 672</option></select></div>
          <div className="setting-row"><span>Preset</span><strong>Thesis baseline v1</strong></div>
          <div className="setting-row"><span>Window</span><strong>{rangeDuration.toFixed(2)} s {(!isBaselineWindow || fps !== 7.5 || frameCount !== 16 || size !== 448) && <small className="experimental">Experimental</small>}</strong></div>
          <p className="preview-note">Frame count and FPS set the window length. Editing the end time recalculates FPS.</p>

          <button className="analyze-button" disabled={!video || !rangeValid || busy || Boolean(active) || (video.source !== "synthetic" && (!visiblePrepared || preparing)) || (video.source === "synthetic" && !simulated)} onClick={analyze}>
            {active ? <><i className="spinner" /> {job?.state === "queued" ? "Queued" : "Analyzing"}</> : busy ? "Please wait…" : result ? "Run again" : "Run analysis"}
          </button>

          {error && <p className="error-message" role="alert">{error}</p>}

          {!job && <div className="result-placeholder"><span>{video ? "Ready when you are" : "Start with a clip"}</span><p>{video?.source === "synthetic" && !simulated ? "Choose a real video for online vLLM analysis." : video ? simulated ? "Check your window, then run a simulated analysis." : "Check your prepared frames, then run analysis." : "Choose a sample, browse the dataset, or upload a video to get started."}</p></div>}
          {active && <p className="job-status" role="status">{job.state === "queued" ? "Your clip is queued for analysis." : simulated ? "Generating a simulated result…" : "Analyzing prepared frames…"}</p>}

          {job?.state === "failed" && <div className="result-card failure" role="alert"><span>Processing failed</span><p>{job.error}</p></div>}

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

      <footer><span>{simulated ? "Local MVP · no model inference is occurring" : "Online vLLM analysis"}</span><span>Every result keeps its input and configuration identity</span></footer>
      <input ref={fileInput} type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska" hidden onChange={onFileChange} />
    </main>
  );
}
