import { useEffect, useState } from "react";
import { getVideo, retryJob } from "./api";
import type { AnalysisJob, Prediction, VideoAsset } from "./api";
import { formatTokenUsage } from "./tokens";
import { samplingSummary } from "./SettingsDialog";
import { runName } from "./runState";
import { generationSummary } from "./experimentState";

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

function wordCount(text: string): string {
  const count = text.split(/\s+/).filter(Boolean).length;
  return `${count.toLocaleString("en-US")} ${count === 1 ? "word" : "words"}`;
}

/** The model's reasoning, if any, and the answer the label was parsed from. */
export function ModelAnswer({ prediction, open = true }: { prediction: Prediction; open?: boolean }) {
  // Predictions saved before the parsed parts were kept show the whole response.
  if (prediction.answer == null) return <p className="raw-response">“{prediction.raw_response}”</p>;
  return <div className="model-answer">
    {prediction.reasoning && <details className="reasoning" open={open}>
      <summary><span>Model reasoning</span><small>{wordCount(prediction.reasoning)}</small></summary>
      <div className="reasoning-text" tabIndex={0}>{prediction.reasoning}</div>
    </details>}
    <p className="answer-line"><span>Parsed from</span><q>{prediction.answer}</q></p>
  </div>;
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
    {activeJob && <p className="job-status" role="status">{activeJob.state === "queued" ? "A submitted clip is queued for analysis." : activeJob.configuration?.backend_kind === "mock" ? "Generating a simulated result…" : "Analyzing prepared frames…"}</p>}

    {!job && <div className="result-placeholder"><p>{currentVideo?.source === "synthetic" && !simulated ? "Choose a real video for online vLLM analysis." : currentVideo ? "Check the window and frames, then run the analysis." : "The result appears here after you run an analysis."}</p></div>}
    {job && <p className="result-source">Submitted source: {jobVideo?.id === job.video_id ? jobVideo.filename : job.video_id} · {job.start_seconds.toFixed(3)}–{job.end_seconds.toFixed(3)} s</p>}

    {job?.state === "failed" && <div className="result-card failure" role="alert"><span>Processing failed</span><p>{job.error}</p><button className="button secondary" onClick={() => retryJob(job.id).then(onRecord).catch((cause) => onError(cause instanceof Error ? cause.message : "Could not retry run"))} disabled={Boolean(activeJob)}>Retry run</button></div>}

    {result && <div className="result-card" aria-live="polite">
      <div className="result-overline"><span>Activity</span>{result.backend_kind === "mock" && <span>Simulated</span>}</div>
      <div className={`result-label ${result.label === "fall" || result.label === "fallen" ? "alert" : ""}`}>{formatLabel(result.label)}</div>
      {result.backend_kind === "mock" && <p className="simulation-note">Simulated label from the mock backend, not inferred from this clip.</p>}
      <ModelAnswer prediction={result} />
      <details className="result-details">
        <summary>Run details</summary>
        <dl>
          <div><dt>Request time</dt><dd>{result.request_duration_ms.toFixed(0)} ms</dd></div>
          <div><dt>Tokens</dt><dd>{formatTokenUsage(result)}</dd></div>
          <div><dt>Model</dt><dd>{result.model}</dd></div>
          {result.fixture_version && <div><dt>Fixture</dt><dd>{result.fixture_version}</dd></div>}
          <div><dt>Prompt preset</dt><dd>{job.configuration?.prompt_preset ?? "Unavailable"}</dd></div>
          <div><dt>Strategy</dt><dd>{job.configuration ? job.configuration.chain_of_thought ? "Chain of thought" : "Direct answer" : "Unavailable"}</dd></div>
          <div><dt>LLM sampling</dt><dd>{job.configuration ? generationSummary(job.configuration.generation) : "Unavailable"}</dd></div>
          {job.configuration && <div><dt>Preprocessing</dt><dd>{samplingSummary(job.configuration.preprocessing.frames, job.configuration.preprocessing.fps, job.configuration.preprocessing.resize)}</dd></div>}
          <div><dt>Sample times</dt><dd title={result.sampled_timestamps.join(", ")}>{result.sampled_timestamps.map((timestamp) => timestamp.toFixed(3)).join(", ")} s</dd></div>
          <div><dt>Configuration</dt><dd title={job.configuration_id}>{job.configuration_id}</dd></div>
          {job.prepared_input_id && <div><dt>Prepared input</dt><dd title={job.prepared_input_id}>{job.prepared_input_id}</dd></div>}
        </dl>
        {job.configuration && <><h3>Saved prompt</h3><pre className="saved-prompt">{job.configuration.prompt_text}</pre></>}
      </details>
    </div>}

    {job && <section className="recent-runs" aria-label="Recent runs">
      <div className="recent-runs-head">
        {jobs.length > 1 ? <label htmlFor="recent-run">Recent runs</label> : <span>This run</span>}
        <span className="download-links"><span>Export</span><a aria-label="Download run JSON" href={`/api/analysis-jobs/${job.id}/export?format=json`} download>JSON</a><a aria-label="Download run CSV" href={`/api/analysis-jobs/${job.id}/export?format=csv`} download>CSV</a></span>
      </div>
      {jobs.length > 1 && <select id="recent-run" value={selectedJobId ?? ""} onChange={(event) => onSelectJob(event.target.value)}>
        {jobs.map((item) => <option key={item.id} value={item.id}>{runName(item)}</option>)}
      </select>}
    </section>}
  </>;
}
