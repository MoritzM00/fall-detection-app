export type Tone = "done" | "alert" | "active" | "gap" | "failed" | "idle";

// Paint order when windows overlap: a fall must never be hidden under a later, less important window.
const paintOrder: Tone[] = ["gap", "idle", "active", "done", "failed", "alert"];

export function inPaintOrder<T extends { tone: Tone; start: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => paintOrder.indexOf(a.tone) - paintOrder.indexOf(b.tone) || a.start - b.start);
}

/**
 * How far a result's input window ends behind the current position (the live edge or
 * the playback clock), and whether it is older than a result can be while the pipeline
 * keeps up: a window may still start up to the expiration after it ends, its own
 * processing comes on top, and the next result is due one stride later. Past that the
 * page must not let an old non-fall label read as a current all-clear.
 */
export function resultAge(endSeconds: number, position: number, policy: { expirationSeconds: number; strideSeconds: number; processingSeconds: number }) {
  const age = Math.max(0, position - endSeconds);
  return { age, outdated: age > policy.expirationSeconds + policy.strideSeconds + policy.processingSeconds };
}

type WindowLike = { generation: number; segment_id: string; state: string; job_id: string | null; end_seconds: number };
type JobLike = { state: string };
type SessionLike = { generation: number; segment_id: string };

const STAGES: Record<string, string> = {
  pending: "waiting to prepare frames",
  preparing: "preparing frames",
  submitted: "submitted to the model",
  queued: "waiting for the model",
  running: "model running",
};

/** Windows of the current generation and segment still on their way to a result, oldest first. */
export function inFlight<W extends WindowLike>(windows: W[], jobs: Record<string, JobLike>, session: SessionLike) {
  return windows
    .filter(window => window.generation === session.generation && window.segment_id === session.segment_id)
    .map(window => ({ window, state: window.job_id ? jobs[window.job_id]?.state ?? window.state : window.state }))
    .filter(({ state }) => state in STAGES)
    .map(({ window, state }) => ({ window, stage: STAGES[state] }))
    .sort((a, b) => a.window.end_seconds - b.window.end_seconds);
}
