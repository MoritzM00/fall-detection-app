import assert from "node:assert/strict";
import test from "node:test";
import type { AnalysisJob } from "../src/api";
import { activeJobs, fpsForEnd, mergeHistory, submittedEnd, upsertJob } from "../src/runState.ts";

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
