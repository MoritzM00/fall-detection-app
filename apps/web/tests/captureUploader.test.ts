import assert from "node:assert/strict";
import test from "node:test";
import { ApiError } from "../src/api.ts";
import type { CapturedFrame, FrameAck } from "../src/api";
import { MAX_BACKLOG_SECONDS, createCaptureUploader } from "../src/captureUploader.ts";
import type { CaptureStats } from "../src/captureUploader.ts";

const FPS = 15;
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const jpeg = () => Promise.resolve(new Blob([new Uint8Array([0xff, 0xd8])], { type: "image/jpeg" }));
const ack = (frames: CapturedFrame[]): FrameAck => ({ accepted: frames.length, run_seq_high: frames.at(-1)!.run_seq, watermark_seconds: 0, state: "running" });

type Call = { runId: string; frames: CapturedFrame[]; signal: AbortSignal };

function harness(respond: (call: Call, index: number) => Promise<FrameAck>, timeoutMs = 10_000) {
  let clock = 0;
  let ids = 0;
  const calls: Call[] = [];
  const ended: { reason: string; failure: string | null }[] = [];
  const stats: CaptureStats[] = [];
  const uploader = createCaptureUploader({
    captureFps: FPS,
    timeoutMs,
    now: () => clock,
    newRunId: () => `run-${ids++}`,
    post: (runId, frames, signal) => {
      const call = { runId, frames, signal };
      calls.push(call);
      return respond(call, calls.length - 1);
    },
    onStats: value => stats.push(value),
    onEnded: (reason, failure) => ended.push({ reason, failure }),
  });
  return {
    uploader, calls, ended, stats,
    /** Captures `count` frames at the capture rate, pumping uploads as the hook's timer would. */
    async run(count: number) {
      for (let index = 0; index < count; index++) {
        clock += 1000 / FPS;
        uploader.capture(jpeg());
        await settle();
        uploader.pump();
        await settle();
      }
    },
  };
}

/** Never resolves until aborted, like a request whose connection hangs. */
function hang(call: Call): Promise<FrameAck> {
  return new Promise((_, reject) => call.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
}

test("frames are numbered at capture time and uploaded in order even if encoding finishes out of order", async () => {
  const h = harness(async call => ack(call.frames));
  let releaseFirst!: (blob: Blob) => void;
  h.uploader.capture(new Promise(resolve => { releaseFirst = resolve; }));
  h.uploader.capture(jpeg());
  await settle();
  h.uploader.pump();
  assert.equal(h.calls.length, 0, "a later frame must not overtake an unencoded earlier one");
  releaseFirst(new Blob(["x"]));
  await settle();
  h.uploader.pump();
  await settle();
  assert.deepEqual(h.calls[0].frames.map(frame => frame.run_seq), [0, 1]);
  assert.equal(h.uploader.queued, 0);
});

test("a hung upload keeps the queue bounded and capture recovers in a new run behind a gap", async () => {
  const limit = FPS * MAX_BACKLOG_SECONDS;
  const h = harness((call, index) => index === 0 ? hang(call) : Promise.resolve(ack(call.frames)));
  await h.run(limit * 3);
  assert.ok(h.uploader.queued <= limit, `queue grew to ${h.uploader.queued}`);
  // The hung request was abandoned with its run; the next upload starts a new run at run_seq 0.
  assert.equal(h.calls[0].signal.aborted, true);
  assert.equal(h.calls[0].runId, "run-0");
  assert.ok(h.calls.length > 1, "uploads resumed after the hang");
  const resumed = h.calls[1];
  assert.notEqual(resumed.runId, "run-0");
  assert.equal(resumed.frames[0].run_seq, 0);
  assert.equal(resumed.frames[0].capture_seconds, 0);
  assert.deepEqual(h.ended, []);
});

test("an upload timeout is retried with the same frames", async () => {
  const h = harness((call, index) => index === 0 ? hang(call) : Promise.resolve(ack(call.frames)), 5);
  await h.run(3);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(h.calls[0].signal.aborted, true);
  await h.run(8); // Past the 500 ms backoff on the fake clock.
  assert.equal(h.calls[1].runId, h.calls[0].runId);
  assert.equal(h.calls[1].frames[0].run_seq, h.calls[0].frames[0].run_seq);
  assert.deepEqual(h.ended, []);
});

test("a late response from an abandoned run does not change the new run", async () => {
  let release!: (value: FrameAck) => void;
  const h = harness((call, index) => index === 0
    ? new Promise(resolve => { release = resolve; }) // Ignores abort and answers late.
    : Promise.resolve(ack(call.frames)));
  await h.run(FPS * MAX_BACKLOG_SECONDS + 2);
  assert.equal(h.uploader.runId, "run-1");
  const queued = h.uploader.queued;
  release(ack(h.calls[0].frames));
  await settle();
  assert.equal(h.uploader.queued, queued, "the stale acknowledgement must not drop new-run frames");
  h.uploader.pump();
  await settle();
  assert.equal(h.calls[1].runId, "run-1");
  assert.equal(h.calls[1].frames[0].run_seq, 0);
});

test("a 422 ends the run with a readable error instead of being retried", async () => {
  const h = harness(async () => { throw new ApiError("Frames arrive faster than twice the capture rate", 422); });
  await h.run(20);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.ended, [{ reason: "Frames arrive faster than twice the capture rate", failure: "Frame upload failed: Frames arrive faster than twice the capture rate" }]);
  assert.equal(h.uploader.queued, 0);
});

test("transient errors back off and retry; continuity conflicts start a new run; stopping conflicts end quietly", async () => {
  const responses = [
    () => { throw new ApiError("Inference capacity is full", 503); },
    () => { throw new ApiError("Missing frames before run_seq 4; resend from 0", 409); },
    () => { throw new ApiError("Session is not running", 409); },
  ];
  const h = harness(async (_call, index) => responses[index]());
  await h.run(1);
  assert.equal(h.calls.length, 1);
  await h.run(8); // 500 ms backoff on the fake clock.
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].runId, h.calls[0].runId);
  await h.run(2);
  assert.equal(h.calls[2].runId, "run-1");
  assert.equal(h.calls[2].frames[0].run_seq, 0);
  assert.deepEqual(h.ended, [{ reason: "Session is not running", failure: null }]);
});
