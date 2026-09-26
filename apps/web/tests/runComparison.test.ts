import assert from "node:assert/strict";
import test from "node:test";
import type { AnalysisJob } from "../src/api";
import { comparisonRows, samePreparedFrames, sameWindow } from "../src/comparisonState.ts";

function job(id: string, overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id, video_id: "clip", start_seconds: 0, end_seconds: 2, prepared_input_id: "bundle",
    configuration_id: `config-${id}`, state: "succeeded", attempt_count: 1, error: null,
    created_at: "2026-09-26T10:00:00Z", updated_at: "2026-09-26T10:00:01Z",
    configuration: { model: "qwen", prompt_preset: "baseline", prompt_text: "Classify activity", backend_kind: "vllm", fixture_version: null,
      preprocessing: { frames: 16, fps: 7.5, resize: 448, crop: "center", bundle_sha256: "hash" }, generation: { temperature: 0, max_tokens: 32 } },
    prediction: { id: `prediction-${id}`, label: "walk", raw_response: "walk", sampled_timestamps: [0, 1, 2], backend_kind: "vllm", model: "qwen", fixture_version: null,
      request_duration_ms: 100, total_duration_ms: 110, completed_at: "2026-09-26T10:00:01Z" },
    ...overrides,
  };
}

test("comparison identifies changed settings and distinguishes frames from source/window", () => {
  const baseline = job("baseline");
  const edited = job("edited");
  edited.configuration!.prompt_text = "Edited prompt";
  edited.configuration!.generation.temperature = 0.8;
  assert.equal(samePreparedFrames(baseline, edited), true);
  const changed = comparisonRows(baseline, edited).filter((row) => row.changed).map((row) => row.name);
  assert.deepEqual(changed, ["Prompt", "Temperature", "Configuration ID"]);
  assert.equal(baseline.configuration?.prompt_text, "Classify activity");
  assert.equal(sameWindow(baseline, job("different-frames", { prepared_input_id: "other" })), true);
  assert.equal(samePreparedFrames(baseline, job("different-frames", { prepared_input_id: "other" })), false);
  assert.equal(sameWindow(baseline, job("other-source", { video_id: "other" })), false);
  assert.equal(sameWindow(baseline, job("other-window", { end_seconds: 3 })), false);
});

test("failed or legacy runs remain explicit failures with unknown input/configuration", () => {
  const failed = job("failed", { state: "failed", error: "Invalid model output", prediction: null, configuration: null, prepared_input_id: null });
  const baseline = job("baseline");
  assert.equal(samePreparedFrames(failed, baseline), false);
  const rows = comparisonRows(baseline, failed);
  assert.equal(rows.find((row) => row.name === "Status")?.right, "Failed: Invalid model output");
  assert.equal(rows.find((row) => row.name === "Activity")?.right, "No prediction");
  assert.equal(rows.find((row) => row.name === "Prompt")?.right, "Unavailable");
  assert.equal(samePreparedFrames(failed, { ...failed, id: "unknown" }), false);
});
