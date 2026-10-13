import assert from "node:assert/strict";
import test from "node:test";
import type { AnalysisJob } from "../src/api";
import { activeJobs, fpsForEnd, mergeHistory, runName, splitReasoning, submittedEnd, upsertJob } from "../src/runState.ts";

function job(id: string, state: AnalysisJob["state"]): AnalysisJob {
  return { id, state } as AnalysisJob;
}

test("editing or switching source leaves a submitted active run tracked", () => {
  const running = job("first", "running");
  const completed = job("second", "succeeded");
  const history = [completed, running];
  assert.deepEqual(activeJobs(history).map((item) => item.id), ["first"]);
  assert.deepEqual(activeJobs(upsertJob(history, job("first", "failed"))), []);
});

test("a retry replaces the failed run without creating a second identity", () => {
  const history = [job("first", "failed")];
  const next = upsertJob(history, job("first", "queued"));
  assert.equal(next.length, 1);
  assert.deepEqual(activeJobs(next).map((item) => item.id), ["first"]);
});

test("a delayed history response cannot erase a newly recorded active run", () => {
  const locallySubmitted = { ...job("new", "queued"), created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z" };
  assert.deepEqual(activeJobs(mergeHistory([locallySubmitted], [])).map((item) => item.id), ["new"]);
});

test("sampling derives end and rejects invalid windows", () => {
  assert.equal(submittedEnd(0, 6, 5), 1);
  assert.equal(fpsForEnd(0, 1, 6), 5);
  assert.equal(fpsForEnd(1, 0, 6), null);
  assert.equal(fpsForEnd(0, 0.1, 16), null);
});

test("run names show the outcome and window instead of raw identifiers", () => {
  const base = { id: "run-id", created_at: "2026-10-12T16:26:18Z", start_seconds: 0, end_seconds: 2 };
  const done = { ...base, state: "succeeded", prediction: { label: "sit_down" } } as AnalysisJob;
  const failed = { ...base, state: "failed", prediction: null, created_at: "not a date" } as AnalysisJob;
  assert.match(runName(done), / · sit down · 0\.0–2\.0 s$/);
  assert.doesNotMatch(runName(done), /run-id/);
  assert.equal(runName(failed), "not a date · failed · 0.0–2.0 s");
});

test("chain-of-thought responses split into reasoning and the final answer line", () => {
  assert.deepEqual(splitReasoning("Drops fast.\nNo bracing.\n\nThe best answer is: fall\n"), { reasoning: "Drops fast.\nNo bracing.", answer: "The best answer is: fall" });
  assert.deepEqual(splitReasoning("<think>Drops fast.</think>\nThe best answer is: fall"), { reasoning: "Drops fast.", answer: "The best answer is: fall" });
  assert.deepEqual(splitReasoning("Drops fast.\n</think>\nThe best answer is: fall"), { reasoning: "Drops fast.", answer: "The best answer is: fall" });
  assert.deepEqual(splitReasoning("The best answer is: walk"), { reasoning: "", answer: "The best answer is: walk" });
  assert.deepEqual(splitReasoning("  \n"), { reasoning: "", answer: "" });
});
