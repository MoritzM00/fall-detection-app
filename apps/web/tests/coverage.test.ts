import assert from "node:assert/strict";
import test from "node:test";
import { inFlight, inPaintOrder, resultAge } from "../src/coverage.ts";

test("overlapping fall windows are painted above later windows of any other state", () => {
  const painted = inPaintOrder([
    { tone: "alert", start: 0 },
    { tone: "done", start: 1 },
    { tone: "active", start: 2 },
    { tone: "gap", start: 1.5 },
    { tone: "failed", start: 0.5 },
    { tone: "alert", start: 3 },
  ] as const);
  assert.deepEqual(painted.map(item => `${item.tone}@${item.start}`), ["gap@1.5", "active@2", "done@1", "failed@0.5", "alert@0", "alert@3"]);
});

test("a result is outdated once it is older than expiration, stride and its own processing allow", () => {
  const policy = { expirationSeconds: 5, strideSeconds: 2, processingSeconds: 1.5 };
  assert.deepEqual(resultAge(10, 14, policy), { age: 4, outdated: false });
  assert.deepEqual(resultAge(10, 18.5, policy), { age: 8.5, outdated: false });
  assert.deepEqual(resultAge(10, 19, policy), { age: 9, outdated: true });
  // A position behind the window (a seek back) is not a negative age.
  assert.deepEqual(resultAge(10, 8, policy), { age: 0, outdated: false });
});

test("in-flight windows follow their job state and ignore other generations and finished work", () => {
  const session = { generation: 2, segment_id: "s" };
  const window = (id: string, end: number, state: string, job_id: string | null = null, generation = 2) => ({ id, generation, segment_id: "s", state, job_id, end_seconds: end });
  const windows = [
    window("pending", 16, "pending"),
    window("running", 14, "submitted", "job-running"),
    window("done", 12, "submitted", "job-done"),
    window("unloaded", 13, "submitted", "job-unloaded"),
    window("gap", 11, "skipped"),
    window("old", 15, "preparing", null, 1),
  ];
  const jobs = { "job-running": { state: "running" }, "job-done": { state: "succeeded" } };
  assert.deepEqual(inFlight(windows, jobs, session).map(({ window, stage }) => `${window.id}: ${stage}`), [
    "unloaded: submitted to the model",
    "running: model running",
    "pending: waiting to prepare frames",
  ]);
});
