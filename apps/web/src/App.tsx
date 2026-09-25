import { useEffect, useState } from "react";
import { createJob, getCapabilities } from "./api";
import { FramePreview } from "./FramePreview";
import { SamplingSettings, WindowControls } from "./SamplingControls";
import { SourceSelection } from "./SourceSelection";
import { SubmittedResult } from "./SubmittedResult";
import { usePreparation } from "./usePreparation";
import { useRunHistory } from "./useRunHistory";
import { useSamplingSelection } from "./useSamplingSelection";
import { useSourceSelection } from "./useSourceSelection";

export default function App() {
  const sampling = useSamplingSelection();
  const source = useSourceSelection(sampling.reset, sampling.fitDuration);
  const { jobs, selectedJob: job, selectedJobId, activeJob, pollError, ready: historyReady, setSelectedJobId, refresh, record } = useRunHistory();
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<{ simulated: boolean; models: string[]; backend_kind: string } | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const busy = source.busy || analysisBusy;
  const simulated = capabilities?.simulated === true;
  const rangeValid = sampling.isValid(source.duration);
  const { visiblePrepared, preparing, preparationError } = usePreparation(
    source.video, sampling.startSeconds, sampling.frameCount, sampling.fps,
    sampling.size, rangeValid,
  );

  useEffect(() => {
    getCapabilities().then(setCapabilities).catch((cause) => setCapabilityError(cause instanceof Error ? cause.message : "Could not load capabilities"));
  }, []);

  async function analyze() {
    if (!source.video || activeJob || !historyReady) return;
    setAnalysisBusy(true);
    setError(null);
    try {
      const created = await createJob(
        source.video.id, sampling.startSeconds, sampling.endSeconds,
        visiblePrepared?.id ?? null, sampling.frameCount, sampling.fps, sampling.size,
      );
      record(created);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start analysis");
    } finally {
      setAnalysisBusy(false);
    }
  }

  function updateRange(nextStart: number, nextEnd: number) {
    if (busy) return;
    sampling.updateRange(nextStart, nextEnd);
    setError(null);
    source.setError(null);
  }

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#top" aria-label="Sentinel home">
        <span className="brand-mark"><i /><i /><i /></span>
        <span>Sentinel</span>
      </a>
      <div className="mode-tabs" aria-label="Application mode">
        <button className="active">Clip analysis</button>
        <button disabled>Monitoring <small>soon</small></button>
      </div>
      <span className={`system-state ${capabilities ? "" : "offline"}`}><i /> {capabilities ? simulated ? "Local simulation" : "Online vLLM" : "Backend unavailable"}</span>
    </header>

    <section className="intro" id="top">
      <div>
        <h1>Clip analysis</h1>
        <p>Pick a clip and a time window, check the frames the model will see, then classify the activity.</p>
      </div>
    </section>

    <section className="workspace">
      <div className="viewer-panel">
        <SourceSelection
          video={source.video}
          previewUrl={source.previewUrl}
          duration={source.duration}
          busy={busy}
          datasetVideos={source.datasetVideos}
          selectedDatasetPath={source.selectedDatasetPath}
          showDatasetBrowser={source.showDatasetBrowser}
          onSample={() => { setError(null); void source.useSample(); }}
          onBrowseDataset={() => { setError(null); void source.browseDataset(); }}
          onDatasetPathChange={source.setSelectedDatasetPath}
          onOpenDataset={() => { setError(null); void source.useDatasetVideo(); }}
          onUpload={(file) => { setError(null); void source.upload(file); }}
          onDurationLoaded={source.onDurationLoaded}
        />
        {source.video && <>
          <WindowControls
            startSeconds={sampling.startSeconds}
            endSeconds={sampling.endSeconds}
            frameCount={sampling.frameCount}
            fps={sampling.fps}
            duration={source.duration}
            valid={rangeValid}
            busy={busy}
            onRangeChange={updateRange}
            onChangeSource={() => { setError(null); source.clear(); }}
          />
          <FramePreview
            video={source.video}
            frameCount={sampling.frameCount}
            timestamps={sampling.previewTimestamps}
            prepared={visiblePrepared}
            preparing={preparing}
            error={preparationError}
            valid={rangeValid}
          />
        </>}
      </div>

      <aside className="analysis-panel">
        <div className="panel-heading">
          <div><span>02</span><h2>Analysis</h2></div>
          <span className="mock-badge">{capabilities ? simulated ? "Simulated" : "Online vLLM" : "Unavailable"}</span>
        </div>
        <div className="setting-row"><span>Model</span><strong>{capabilities?.models[0] ?? "Unavailable"}</strong></div>
        <SamplingSettings
          frameCount={sampling.frameCount}
          fps={sampling.fps}
          size={sampling.size}
          rangeDuration={sampling.rangeDuration}
          isBaselineWindow={sampling.isBaselineWindow}
          busy={busy}
          onFrameCountChange={sampling.setFrameCount}
          onFpsChange={sampling.setFps}
          onSizeChange={sampling.setSize}
        />

        <button className="analyze-button" disabled={!historyReady || !capabilities || !source.video || !rangeValid || busy || Boolean(activeJob) || (source.video.source !== "synthetic" && (!visiblePrepared || preparing)) || (source.video.source === "synthetic" && !simulated)} onClick={analyze}>
          {activeJob ? <><i className="spinner" /> {activeJob.state === "queued" ? "Queued" : "Analyzing"}</> : busy ? "Please wait…" : job?.prediction && job.video_id === source.video?.id ? "Run again" : "Run analysis"}
        </button>

        {(source.error ?? error) && <p className="error-message" role="alert">{source.error ?? error}</p>}
        {capabilityError && <p className="error-message" role="alert">Backend settings unavailable: {capabilityError}</p>}
        <SubmittedResult
          jobs={jobs}
          job={job}
          selectedJobId={selectedJobId}
          activeJob={activeJob}
          currentVideo={source.video}
          simulated={simulated}
          historyReady={historyReady}
          pollError={pollError}
          onSelectJob={setSelectedJobId}
          onRefresh={() => void refresh()}
          onRecord={record}
          onError={setError}
        />
      </aside>
    </section>

    <footer><span>{capabilities ? simulated ? "Results are simulated by a mock backend, not produced by a model." : "Results come from the online vLLM backend." : "Backend settings unavailable."}</span><span>Every result keeps its input window and configuration.</span></footer>
  </main>;
}
