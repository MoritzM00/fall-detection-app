import { useEffect, useMemo, useRef, useState } from "react";
import { createJob, createSample, getJob, uploadVideo } from "./api";
import type { AnalysisJob, VideoAsset } from "./api";

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

function UploadedFrame({ src, timestamp }: { src: string; timestamp: number }) {
  const frame = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const seek = () => {
      element.currentTime = Math.min(timestamp, Math.max(0, element.duration - 0.001));
    };
    if (element.readyState >= 1) seek();
    else element.addEventListener("loadedmetadata", seek, { once: true });
    return () => element.removeEventListener("loadedmetadata", seek);
  }, [src, timestamp]);

  return <video ref={frame} src={src} muted playsInline preload="metadata" aria-hidden="true" />;
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
  const fileInput = useRef<HTMLInputElement>(null);

  const previewTimestamps = useMemo(
    () => endSeconds > startSeconds ? sampleTimestamps(startSeconds, endSeconds) : [],
    [startSeconds, endSeconds],
  );
  const rangeDuration = endSeconds - startSeconds;
  const isBaselineWindow = Math.abs(rangeDuration - 2) < 0.001;

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  useEffect(() => {
    if (!job || terminalStates.has(job.state)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getJob(job.id);
        setJob(next);
      } catch (pollError) {
        setError(pollError instanceof Error ? pollError.message : "Could not read job status");
      }
    }, 500);
    return () => window.clearInterval(timer);
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
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    if (!video) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await createJob(video.id, startSeconds, endSeconds));
    } catch (jobError) {
      setError(jobError instanceof Error ? jobError.message : "Could not start analysis");
    } finally {
      setBusy(false);
    }
  }

  const active = job && !terminalStates.has(job.state);
  const result = job?.prediction;
  const rangeValid = startSeconds >= 0 && endSeconds > startSeconds && rangeDuration <= 30 && (duration === null || endSeconds <= duration);

  function updateRange(nextStart: number, nextEnd: number) {
    setStartSeconds(nextStart);
    setEndSeconds(nextEnd);
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
        <span className="system-state"><i /> Local simulation</span>
      </header>

      <section className="intro" id="top">
        <div>
          <span className="eyebrow">Activity intelligence · prototype 01</span>
          <h1>See the moment.<br /><em>Understand the motion.</em></h1>
        </div>
        <p>Analyze a short video window through the same pipeline that will later connect to the GPU model.</p>
      </section>

      <section className="workspace">
        <div className="viewer-panel">
          <div className="panel-heading">
            <div><span>01</span><h2>Source</h2></div>
            {video && <span className="asset-pill">{video.source === "synthetic" ? "Synthetic" : "Uploaded"}</span>}
          </div>

          <div className={`viewer ${video ? "loaded" : ""}`}>
            {!video && (
              <div className="empty-state">
                <span className="upload-glyph">↗</span>
                <h3>Choose a clip</h3>
                <p>Use the built-in scenario now, or bring an MP4, MOV, WebM, or MKV.</p>
                <div className="source-actions">
                  <button className="button primary" onClick={useSample} disabled={busy}>Use sample clip</button>
                  <button className="button secondary" onClick={() => fileInput.current?.click()} disabled={busy}>Upload video</button>
                </div>
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
            {video?.source === "upload" && previewUrl && (
              <video
                src={previewUrl}
                controls
                onLoadedMetadata={(event) => {
                  const nextDuration = event.currentTarget.duration;
                  if (!Number.isFinite(nextDuration)) return;
                  setDuration(nextDuration);
                  setEndSeconds(Math.min(2, nextDuration));
                }}
              />
            )}
          </div>

          {video && (
            <>
              <div className="clip-controls">
                <div><span className="control-label">Analysis window</span><strong>{formatTime(startSeconds)} — {formatTime(endSeconds)}</strong></div>
                <div className="range-fields">
                  <label>Start <input type="number" min="0" max={Math.max(0, endSeconds - 0.1)} step="0.1" value={startSeconds} onChange={(event) => { if (Number.isFinite(event.currentTarget.valueAsNumber)) updateRange(event.currentTarget.valueAsNumber, endSeconds); }} /></label>
                  <span>to</span>
                  <label>End <input type="number" min={startSeconds + 0.1} max={duration ?? 30} step="0.1" value={endSeconds} onChange={(event) => { if (Number.isFinite(event.currentTarget.valueAsNumber)) updateRange(startSeconds, event.currentTarget.valueAsNumber); }} /></label>
                </div>
                <button className="text-button" onClick={() => { setVideo(null); setJob(null); setPreviewUrl(null); setDuration(null); }}>Change source</button>
              </div>
              {!rangeValid && <p className="range-error" role="alert">Choose a valid window of up to 30 seconds within the clip.</p>}

              <section className="frame-preview" aria-labelledby="frame-preview-title">
                <div className="frame-preview-heading">
                  <div><span className="control-label">Sample preview</span><strong id="frame-preview-title">16 timestamped frames</strong></div>
                  <span className="preview-disclaimer">Visual preview only · center crop</span>
                </div>
                {rangeValid && <div className="frame-strip">
                  {previewTimestamps.map((timestamp, index) => (
                    <figure className="sample-frame" key={`${timestamp}-${index}`}>
                      <div className="frame-image">
                        {video.source === "upload" && previewUrl ? (
                          <UploadedFrame src={previewUrl} timestamp={timestamp} />
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
                <p className="preview-note">Preview timestamps match the planned sampler. The simulated backend does not yet decode these frames for inference.</p>
              </section>
            </>
          )}
        </div>

        <aside className="analysis-panel">
          <div className="panel-heading">
            <div><span>02</span><h2>Analysis</h2></div>
            <span className="mock-badge">Simulated</span>
          </div>

          <div className="setting-row"><span>Model</span><strong>Qwen3-VL 8B</strong></div>
          <div className="setting-row"><span>Frames</span><strong>16 · center crop</strong></div>
          <div className="setting-row"><span>Preset</span><strong>Thesis baseline v1</strong></div>
          <div className="setting-row"><span>Window</span><strong>{rangeDuration.toFixed(1)} s {!isBaselineWindow && <small className="experimental">Experimental</small>}</strong></div>

          <button className="analyze-button" disabled={!video || !rangeValid || busy || Boolean(active)} onClick={analyze}>
            {active ? <><i className="spinner" /> {job?.state === "queued" ? "Queued" : "Analyzing"}</> : "Run analysis"}
          </button>

          {error && <p className="error-message" role="alert">{error}</p>}

          {!job && <div className="result-placeholder"><span>Result</span><p>Your prediction will appear here with its provenance and timing.</p></div>}

          {job?.state === "failed" && <div className="result-card failure" role="alert"><span>Processing failed</span><p>{job.error}</p></div>}

          {result && (
            <div className="result-card" aria-live="polite">
              <div className="result-overline"><span>Detected activity</span><span>SIMULATED</span></div>
              <div className={`result-label ${result.label === "fall" || result.label === "fallen" ? "alert" : ""}`}>{formatLabel(result.label)}</div>
              <p className="raw-response">“{result.raw_response}”</p>
              <dl>
                <div><dt>Model</dt><dd>{result.model}</dd></div>
                <div><dt>Fixture</dt><dd>{result.fixture_version}</dd></div>
                <div><dt>Configuration</dt><dd title={job.configuration_id}>{job.configuration_id}</dd></div>
                <div><dt>Input window</dt><dd>{job.start_seconds.toFixed(3)}–{job.end_seconds.toFixed(3)} s</dd></div>
                <div><dt>Sample times</dt><dd title={result.sampled_timestamps.join(", ")}>{result.sampled_timestamps.map((timestamp) => timestamp.toFixed(3)).join(", ")} s</dd></div>
                <div><dt>Request</dt><dd>{result.request_duration_ms.toFixed(0)} ms</dd></div>
                <div><dt>Frames</dt><dd>{result.sampled_timestamps.length}</dd></div>
              </dl>
            </div>
          )}
        </aside>
      </section>

      <footer><span>Local MVP · no model inference is occurring</span><span>Every result keeps its input and configuration identity</span></footer>
      <input ref={fileInput} type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska" hidden onChange={onFileChange} />
    </main>
  );
}
