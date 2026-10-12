import { MonitoringReplay } from "./MonitoringReplay";
import { useEffect, useState } from "react";
import { createJob, getCapabilities } from "./api";
import { FramePreview } from "./FramePreview";
import { ExperimentControls, baselineExperiment, experimentValid as isExperimentValid, isBaselineExperiment } from "./ExperimentControls";
import { RunComparison } from "./RunComparison";
import { ServingBadge } from "./ServingBadge";
import { privacyNotice } from "./privacy";
import type { Capabilities, ExperimentSettings } from "./api";
import { SamplingSettings, WindowControls } from "./SamplingControls";
import { BASELINE_SAMPLING, SettingsDialog, SettingsSummary, isBaselineSampling, samplingSummary } from "./SettingsDialog";
import { SourceSelection } from "./SourceSelection";
import { SubmittedResult } from "./SubmittedResult";
import { usePreparation } from "./usePreparation";
import { useRunHistory } from "./useRunHistory";
import { useSamplingSelection } from "./useSamplingSelection";
import { useSourceSelection } from "./useSourceSelection";

export default function App() {
  const [mode, setMode] = useState(() => localStorage.getItem("sentinel-mode") ?? "clip");
  function selectMode(value: string) { setMode(value); localStorage.setItem("sentinel-mode", value); }
  const sampling = useSamplingSelection();
  const source = useSourceSelection(sampling.reset, sampling.fitDuration);
  const { jobs, selectedJob: job, selectedJobId, activeJob, pollError, ready: historyReady, setSelectedJobId, refresh, record } = useRunHistory();
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [experiment, setExperiment] = useState<ExperimentSettings | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const busy = source.busy || analysisBusy;
  const simulated = capabilities?.simulated === true;
  const rangeValid = sampling.isValid(source.duration);
  const experimentValid = experiment !== null && isExperimentValid(experiment, capabilities);
  const baseline = Boolean(capabilities && experiment && isBaselineExperiment(capabilities, experiment)) && isBaselineSampling(sampling.frameCount, sampling.fps, sampling.size);
  const { visiblePrepared, preparing, preparationError, retryPreparation } = usePreparation(
    source.video, sampling.startSeconds, sampling.frameCount, sampling.fps,
    sampling.size, rangeValid,
  );

  useEffect(() => {
    getCapabilities().then((loaded) => {
      setCapabilities(loaded);
      setExperiment(baselineExperiment(loaded));
    }).catch((cause) => setCapabilityError(cause instanceof Error ? cause.message : "Could not load capabilities"));
  }, []);

  async function analyze() {
    if (!source.video || activeJob || !historyReady || !experiment || !experimentValid) return;
    setAnalysisBusy(true);
    setError(null);
    try {
      const created = await createJob(
        source.video.id, sampling.startSeconds, sampling.endSeconds,
        visiblePrepared?.id ?? null, sampling.frameCount, sampling.fps, sampling.size,
        experiment,
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

  function resetSettings() {
    sampling.setFrameCount(BASELINE_SAMPLING.frames);
    sampling.setFps(BASELINE_SAMPLING.fps);
    sampling.setSize(BASELINE_SAMPLING.size);
    if (capabilities) setExperiment(baselineExperiment(capabilities));
  }

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#top" aria-label="Sentinel home">
        <span className="brand-mark"><i /><i /><i /></span>
        <span>Sentinel</span>
      </a>
      <div className="mode-tabs" aria-label="Application mode">
        <button className={mode === "clip" ? "active" : ""} aria-pressed={mode === "clip"} onClick={() => selectMode("clip")}>Clip analysis</button>
        <button className={mode === "monitoring" ? "active" : ""} aria-pressed={mode === "monitoring"} onClick={() => selectMode("monitoring")}>Monitoring</button>
      </div>
      {capabilities && !simulated
        ? <ServingBadge />
        : <span className={`system-state ${capabilities ? "" : "offline"}`} title={capabilities ? "Results come from a mock backend, not a model." : capabilityError ?? undefined}><i /> {capabilities ? "Local simulation" : "Backend unavailable"}</span>}
    </header>
    {privacyNotice(capabilities) && <p className="privacy-notice" role="note">{privacyNotice(capabilities)}</p>}

    <div hidden={mode !== "clip"} id="top">
    <h1 className="sr-only">Clip analysis</h1>
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
          onChangeSource={() => { setError(null); source.clear(); }}
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
          />
          <FramePreview
            video={source.video}
            frameCount={sampling.frameCount}
            timestamps={sampling.previewTimestamps}
            prepared={visiblePrepared}
            preparing={preparing}
            error={preparationError}
            onRetry={retryPreparation}
            valid={rangeValid}
          />
        </>}
      </div>

      <aside className="analysis-panel">
        <div className="panel-heading">
          <div><span>02</span><h2>Analysis</h2></div>
        </div>
        <SettingsSummary
          baseline={baseline}
          invalid={Boolean(experiment) && !experimentValid}
          detail={capabilities ? samplingSummary(sampling.frameCount, sampling.fps, sampling.size) : "Backend settings unavailable"}
          disabled={!capabilities || !experiment}
          onEdit={() => setSettingsOpen(true)}
        />

        <button className="analyze-button" disabled={!historyReady || !capabilities || !experimentValid || !source.video || !rangeValid || busy || Boolean(activeJob) || (source.video.source !== "synthetic" && (!visiblePrepared || preparing)) || (source.video.source === "synthetic" && !simulated)} onClick={analyze}>
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

    <RunComparison key={job?.id ?? "no-run"} jobs={jobs} selectedJob={job} />

    <SettingsDialog open={settingsOpen} title="Analysis settings" onClose={() => setSettingsOpen(false)} footer={<>
      <button type="button" className="text-button" disabled={busy || baseline} onClick={resetSettings}>Reset to thesis baseline</button>
      <button type="button" className="button primary" onClick={() => setSettingsOpen(false)}>Done</button>
    </>}>
      <SamplingSettings
        frameCount={sampling.frameCount}
        fps={sampling.fps}
        size={sampling.size}
        rangeDuration={sampling.rangeDuration}
        busy={busy}
        onFrameCountChange={sampling.setFrameCount}
        onFpsChange={sampling.setFps}
        onSizeChange={sampling.setSize}
      />
      {capabilities && experiment && <ExperimentControls capabilities={capabilities} settings={experiment} busy={busy} onChange={setExperiment} />}
      {experiment && !experimentValid && <p className="error-message" role="alert">Enter a nonblank prompt, temperature from 0 to 2, and an integer token limit from {capabilities?.generation_limits.min_max_tokens ?? 16} to {capabilities?.generation_limits.max_max_tokens ?? 4096}.</p>}
    </SettingsDialog>
    </div>
    {mode === "monitoring" && <MonitoringReplay capabilities={capabilities} />}
  </main>;
}
