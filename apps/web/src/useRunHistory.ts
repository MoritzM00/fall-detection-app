import { useEffect, useState } from "react";
import { getJob, listJobs } from "./api.ts";
import type { AnalysisJob } from "./api";
import { activeJobs, mergeHistory, upsertJob } from "./runState.ts";

export function useRunHistory() {
  const [jobs, setJobs] = useState<AnalysisJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const selectedJob = jobs.find((item) => item.id === selectedJobId) ?? null;
  const activeJob = activeJobs(jobs)[0] ?? null;

  async function refresh() {
    try {
      const recent = await listJobs();
      setJobs((current) => mergeHistory(current, recent));
      setSelectedJobId((previous) => previous ?? recent[0]?.id ?? null);
      setPollError(null);
      setReady(true);
    } catch (cause) {
      setPollError(cause instanceof Error ? cause.message : "Could not load recent runs");
    }
  }

  useEffect(() => { void refresh(); }, []);
  const activeIds = activeJobs(jobs).map((item) => item.id).join(",");
  useEffect(() => {
    if (!activeIds) return;
    let cancelled = false;
    let pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const updated = await Promise.all(activeIds.split(",").map(getJob));
        if (!cancelled) {
          setJobs((current) => current.map((old) => updated.find((item) => item.id === old.id) ?? old));
          setPollError(null);
        }
      } catch (cause) {
        if (!cancelled) setPollError(cause instanceof Error ? cause.message : "Could not read job status");
      } finally {
        pending = false;
      }
    };
    const timer = window.setInterval(poll, 500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeIds]);

  function record(next: AnalysisJob) {
    setJobs((current) => upsertJob(current, next));
    setSelectedJobId(next.id);
  }

  return { jobs, selectedJob, selectedJobId, activeJob, pollError, ready, setSelectedJobId, refresh, record };
}
