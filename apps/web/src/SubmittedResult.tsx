import { useEffect, useState } from "react";
import { getVideo, retryJob } from "./api";
import type { AnalysisJob, VideoAsset } from "./api";

type Props = {
  jobs: AnalysisJob[];
  job: AnalysisJob | null;
  selectedJobId: string | null;
  activeJob: AnalysisJob | null;
  currentVideo: VideoAsset | null;
  simulated: boolean;
  historyReady: boolean;
  pollError: string | null;
  onSelectJob: (id: string) => void;
  onRefresh: () => void;
  onRecord: (job: AnalysisJob) => void;
  onError: (message: string) => void;
};

function formatLabel(label: string): string {
  return label.replaceAll("_", " ");
}

export function SubmittedResult({
  jobs, job, selectedJobId, activeJob, currentVideo, simulated,
  historyReady, pollError, onSelectJob, onRefresh, onRecord, onError,
}: Props) {
  const [jobVideo, setJobVideo] = useState<VideoAsset | null>(null);
  useEffect(() => {
    if (!job) { setJobVideo(null); return; }
    setJobVideo(null);
    let cancelled = false;
    getVideo(job.video_id).then((asset) => { if (!cancelled) setJobVideo(asset); })
      .catch(() => { if (!cancelled) setJobVideo(null); });
    return () => { cancelled = true; };
  }, [job?.video_id]);

  const result = job?.prediction;
  return <>
    {!historyReady && !pollError && <p className="job-status" role="status">Recovering recent runs…</p>}
    {pollError && <p className="error-message" role="alert">Run status unavailable: {pollError} <button onClick={onRefresh}>Retry</button></p>}

    {jobs.length > 0 && <section className="recent-runs" aria-label="Recent runs">
      <label htmlFor="recent-run">Recent runs</label>
      <select id="recent-run" value={selectedJobId ?? ""} onChange={(event) => onSelectJob(event.target.value)}>
        {jobs.map((item) => <option key={item.id} value={item.id}>{item.created_at.slice(0, 19)} · {item.state} · {item.video_id}</option>)}
      </select>
    </section>}
    {job && <p className="preview-note">Submitted source: {jobVideo?.id === job.video_id ? jobVideo.filename : job.video_id} · {job.start_seconds.toFixed(3)}–{job.end_seconds.toFixed(3)} s · {job.configuration?.model ?? "Saved model unavailable"}</p>}

    {!job && <div className="result-placeholder"><span>{currentVideo ? "Ready when you are" : "Start with a clip"}</span><p>{currentVideo?.source === "synthetic" && !simulated ? "Choose a real video for online vLLM analysis." : currentVideo ? simulated ? "Check your window, then run a simulated analysis." : "Check your prepared frames, then run analysis." : "Choose a sample, browse the dataset, or upload a video to get started."}</p></div>}
    {activeJob && <p className="job-status" role="status">{activeJob.state === "queued" ? "A submitted clip is queued for analysis." : activeJob.configuration?.backend_kind === "mock" ? "Generating a simulated result…" : "Analyzing prepared frames…"}</p>}

    {job?.state === "failed" && <div className="result-card failure" role="alert"><span>Processing failed</span><p>{job.error}</p><button className="button secondary" onClick={() => retryJob(job.id).then(onRecord).catch((cause) => onError(String(cause)))} disabled={Boolean(activeJob)}>Retry run</button></div>}

    {result && <div className="result-card" aria-live="polite">
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
    </div>}
  </>;
}
