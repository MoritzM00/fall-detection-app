import type { AnalysisJob } from "./api";

export function sameWindow(left: AnalysisJob, right: AnalysisJob): boolean {
  return left.video_id === right.video_id && Math.abs(left.start_seconds - right.start_seconds) <= 0.001
    && Math.abs(left.end_seconds - right.end_seconds) <= 0.001;
}

export function comparisonRows(left: AnalysisJob, right: AnalysisJob) {
  const fields = (job: AnalysisJob): [string, string][] => {
    const config = job.configuration;
    const result = job.prediction;
    return [
      ["Status", job.state === "failed" ? `Failed: ${job.error ?? "Unknown error"}` : job.state],
      ["Activity", result?.label.replaceAll("_", " ") ?? "No prediction"],
      ["Backend", config?.backend_kind ?? "Unavailable"],
      ["Model", config?.model ?? "Unavailable"],
      ["Prompt preset", config?.prompt_preset ?? "Unavailable"],
      ["Prompt", config?.prompt_text ?? "Unavailable"],
      ["Temperature", config ? String(config.generation.temperature) : "Unavailable"],
      ["Max tokens", config ? String(config.generation.max_tokens) : "Unavailable"],
      ["Frames / FPS / crop", config ? `${config.preprocessing.frames} / ${config.preprocessing.fps} / ${config.preprocessing.resize}px ${config.preprocessing.crop}` : "Unavailable"],
      ["Prepared input", job.prepared_input_id ?? "No saved frame bundle"],
      ["Bundle hash", config?.preprocessing.bundle_sha256 ?? "Unavailable"],
      ["Sample times (s)", result?.sampled_timestamps.join(", ") ?? "No prediction"],
      ["Mock fixture", config?.fixture_version ?? "None / unknown"],
      ["Request time", result ? `${result.request_duration_ms.toFixed(0)} ms` : "No prediction"],
      ["Total processing time", result ? `${result.total_duration_ms.toFixed(0)} ms` : "No prediction"],
      ["Raw response", result?.raw_response ?? "No prediction"],
      ["Configuration ID", job.configuration_id],
    ];
  };
  const rightFields = fields(right);
  return fields(left).map(([name, value], index) => ({ name, left: value, right: rightFields[index][1], changed: value !== rightFields[index][1] }));
}

export function samePreparedFrames(left: AnalysisJob, right: AnalysisJob): boolean {
  return sameWindow(left, right) && Boolean(left.prepared_input_id && left.prepared_input_id === right.prepared_input_id);
}
