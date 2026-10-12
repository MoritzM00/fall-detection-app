import { ApiError } from "./api.ts";
import type { CapturedFrame, FrameAck } from "./api";

export type CaptureStats = { frames: number; fps: number | null; lagSeconds: number | null; queued: number };

export const BATCH_FRAMES = 32;
// A backlog beyond this many seconds is abandoned; the next run records the gap explicitly.
export const MAX_BACKLOG_SECONDS = 10;
export const UPLOAD_TIMEOUT_MS = 10_000;
const STOPPING_CONFLICT = /not running|superseded|maximum session length/;

type QueuedFrame = { run_seq: number; capture_seconds: number; blob: Blob | null };

export type CaptureUploaderOptions = {
  captureFps: number;
  post: (runId: string, frames: CapturedFrame[], signal: AbortSignal) => Promise<FrameAck>;
  onStats: (stats: CaptureStats) => void;
  /** Capture is over: `failure` is a readable error when a request was refused for good. */
  onEnded: (reason: string, failure: string | null) => void;
  now?: () => number;
  newRunId?: () => string;
  timeoutMs?: number;
};

/** Transient statuses are retried; any other 4xx means resending the same frames cannot succeed. */
function permanent(cause: unknown): cause is ApiError {
  return cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408 && cause.status !== 429;
}

/**
 * Upload side of one live capture: numbers frames by run at capture time, keeps the
 * queue bounded and sends contiguous, already-encoded prefixes in order. Encoding may
 * finish out of order; `run_seq` is fixed when the frame is captured, so upload order
 * never depends on encoder timing. Starting a new run drops the backlog and aborts the
 * outstanding request; responses for an earlier run never change the current run.
 */
export function createCaptureUploader(options: CaptureUploaderOptions) {
  const now = options.now ?? (() => performance.now());
  const newRunId = options.newRunId ?? (() => crypto.randomUUID());
  const timeoutMs = options.timeoutMs ?? UPLOAD_TIMEOUT_MS;
  const maxQueued = Math.max(BATCH_FRAMES, Math.ceil(options.captureFps * MAX_BACKLOG_SECONDS));
  let runId = newRunId();
  let start = now();
  let nextSeq = 0;
  let queue: QueuedFrame[] = [];
  let inFlight: AbortController | null = null;
  let ended = false;
  let retryAt = 0;
  let backoff = 0;
  let uploaded = 0;
  let lagSeconds: number | null = null;

  function newRun() {
    inFlight?.abort(); // The stale request settles on its own; its result is ignored.
    runId = newRunId();
    start = now();
    nextSeq = 0;
    queue = [];
    retryAt = 0;
    backoff = 0;
  }

  function end(reason: string, failure: string | null) {
    if (ended) return;
    ended = true;
    queue = [];
    inFlight?.abort();
    options.onEnded(reason, failure);
  }

  function report() {
    const elapsed = (now() - start) / 1000;
    options.onStats({ frames: uploaded, fps: elapsed > 1 ? nextSeq / elapsed : null, lagSeconds, queued: queue.length });
  }

  /** Records one frame now; `encoded` may resolve later and in any order. */
  function capture(encoded: Promise<Blob>) {
    if (ended) return;
    if (queue.length >= maxQueued) newRun();
    const frame: QueuedFrame = { run_seq: nextSeq++, capture_seconds: (now() - start) / 1000, blob: null };
    const run = runId;
    queue.push(frame);
    encoded.then(blob => { frame.blob = blob; }, () => {
      // A frame that cannot be encoded breaks run_seq continuity; restart behind a gap.
      if (run === runId && queue.includes(frame)) newRun();
    });
  }

  /** Starts the next upload when nothing is in flight and encoded frames are ready. */
  function pump() {
    if (ended || inFlight || now() < retryAt) return;
    let ready = 0;
    while (ready < queue.length && ready < BATCH_FRAMES && queue[ready].blob) ready++;
    if (!ready) return;
    const batch = queue.slice(0, ready).map(({ run_seq, capture_seconds, blob }) => ({ run_seq, capture_seconds, blob: blob! }));
    const run = runId;
    const controller = new AbortController();
    inFlight = controller;
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    options.post(run, batch, controller.signal).then(ack => {
      uploaded += ack.accepted;
      if (ack.state !== "running") { end(`Session ${ack.state}`, null); return; }
      if (run !== runId || ended) return;
      queue = queue.slice(batch.length);
      backoff = 0;
      lagSeconds = Math.max(0, (now() - start) / 1000 - batch[batch.length - 1].capture_seconds);
      report();
    }, (cause: unknown) => {
      if (cause instanceof ApiError && cause.status === 409 && STOPPING_CONFLICT.test(cause.message)) { end(cause.message, null); return; }
      if (run !== runId || ended) return;
      if (cause instanceof ApiError && cause.status === 409) { newRun(); return; } // Lost continuity; restart cleanly.
      if (permanent(cause)) { end(cause.message, `Frame upload failed: ${cause.message}`); return; }
      backoff = Math.min(5000, backoff ? backoff * 2 : 500);
      retryAt = now() + backoff;
      report();
    }).finally(() => {
      clearTimeout(timer);
      if (inFlight === controller) inFlight = null;
    });
  }

  return {
    capture,
    pump,
    close() { ended = true; inFlight?.abort(); },
    get runId() { return runId; },
    get queued() { return queue.length; },
    get uploading() { return inFlight !== null; },
  };
}
