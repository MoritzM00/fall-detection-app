import type { AnalysisJob } from "./api";

export function isActive(job: AnalysisJob): boolean {
  return job.state === "queued" || job.state === "running";
}

export function upsertJob(jobs: AnalysisJob[], next: AnalysisJob): AnalysisJob[] {
  const remaining = jobs.filter((job) => job.id !== next.id);
  return [next, ...remaining];
}

export function activeJobs(jobs: AnalysisJob[]): AnalysisJob[] {
  return jobs.filter(isActive);
}

export function mergeHistory(current: AnalysisJob[], fetched: AnalysisJob[]): AnalysisJob[] {
  const byId = new Map(fetched.map((job) => [job.id, job]));
  for (const job of current) {
    const remote = byId.get(job.id);
    if (!remote || job.updated_at >= remote.updated_at) byId.set(job.id, job);
  }
  return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function submittedEnd(start: number, count: number, fps: number): number {
  return start + (count - 1) / fps;
}

export function fpsForEnd(start: number, end: number, count: number): number | null {
  const fps = (count - 1) / (end - start);
  return Number.isFinite(fps) && fps > 0 && fps <= 30 ? fps : null;
}

/** Readable run picker entry: creation time, outcome, and input window. */
export function runName(job: AnalysisJob): string {
  const created = new Date(job.created_at);
  const time = Number.isNaN(created.getTime()) ? job.created_at.slice(0, 19) : created.toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
  const outcome = job.prediction ? job.prediction.label.replaceAll("_", " ") : job.state;
  return `${time}, ${outcome}, ${job.start_seconds.toFixed(1)}–${job.end_seconds.toFixed(1)} s`;
}
