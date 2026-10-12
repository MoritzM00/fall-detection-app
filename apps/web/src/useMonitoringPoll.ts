import { useEffect, useRef, useState } from "react";
import { CLIENT_HEADERS, commandSession, getJob, getSession, getVideo, getWindows, listSessions } from "./api";
import type { AnalysisJob, MonitoringCommand, MonitoringSession, MonitoringWindow, VideoAsset } from "./api";

export const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const unfinished = (job: AnalysisJob) => ["queued", "running"].includes(job.state);
const pauseCommand = (): MonitoringCommand => ({ action: "pause", command_id: crypto.randomUUID() });

export type PollEvents = {
  /** A new configuration segment was read; mirror its saved settings in the editor. */
  hydrate(next: MonitoringSession): void;
  /** The first read after a selection or a lost connection; playback restarts from here. */
  recover(next: MonitoringSession): void;
};

/**
 * Selected session, its coverage history and jobs, polled every second.
 *
 * Every selection, and every command except position reports, advances `fence`;
 * a response whose token no longer matches it is discarded. The refs are shared
 * with `useSessionCommands`, which sends commands against the same fence.
 */
export function useMonitoringPoll(events: PollEvents) {
  const [sessions, setSessions] = useState<MonitoringSession[]>([]);
  const [id, setId] = useState(() => localStorage.getItem("sentinel-session") ?? "");
  const [session, setSession] = useState<MonitoringSession | null>(null);
  const [video, setVideo] = useState<VideoAsset | null>(null);
  const [windows, setWindows] = useState<MonitoringWindow[]>([]);
  const [jobs, setJobs] = useState<Record<string, AnalysisJob>>({});
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [updated, setUpdated] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [historyComplete, setHistoryComplete] = useState(false);
  const media = useRef<HTMLVideoElement>(null);
  const fence = useRef(0);
  const current = useRef<MonitoringSession | null>(null);
  const pending = useRef(false);
  const positionPending = useRef(false);
  const retry = useRef<{ id: string; command: MonitoringCommand } | null>(null);
  const initialPosition = useRef(true);
  const hydrated = useRef("");
  const polling = useRef(false);
  const jobCache = useRef<Record<string, AnalysisJob>>({});
  const handlers = useRef(events);
  handlers.current = events;
  current.current = session;

  function adopt(next: MonitoringSession) {
    current.current = next;
    setSession(next);
    if (next.state !== "running") media.current?.pause();
  }
  function select(next: string) {
    const leaving = current.current;
    if (leaving?.state === "running") void commandSession(leaving.id, pauseCommand()).catch(() => {});
    media.current?.pause();
    fence.current++;
    initialPosition.current = true;
    retry.current = null; setError(null);
    current.current = null; jobCache.current = {};
    setSession(null); setVideo(null); setWindows([]); setJobs({}); setConnected(false); setHistoryComplete(false);
    setId(next); localStorage.setItem("sentinel-session", next);
  }

  async function poll(token: number) {
    if ((pending.current && !positionPending.current) || polling.current) return;
    polling.current = true;
    try {
      const all = await listSessions();
      if (token !== fence.current) return;
      setSessions(all);
      if (!id) { setConnected(true); return; }
      const next = await getSession(id);
      const history = await getWindows(id);
      const asset = await getVideo(next.video_id);
      // Unfinished jobs are refreshed even after their window leaves the newest history page.
      const open = Object.values(jobCache.current).filter(unfinished).map(job => job.id);
      const jobIds = [...new Set([
        ...history.flatMap(w => w.job_id ? [w.job_id] : []),
        ...(next.latest_job_id ? [next.latest_job_id] : []),
        ...open,
      ])];
      const loaded = await Promise.all(jobIds.filter(jobId => {
        const cached = jobCache.current[jobId];
        return !cached || unfinished(cached);
      }).map(getJob));
      if (token !== fence.current || (pending.current && !positionPending.current)) return;
      const saved = current.current;
      if (saved && (next.generation !== saved.generation || next.segment_id !== saved.segment_id)) {
        media.current?.pause(); initialPosition.current = true;
      }
      if (saved?.generation === next.generation && saved.segment_id === next.segment_id) next.position = Math.max(next.position, saved.position);
      Object.assign(jobCache.current, Object.fromEntries(loaded.map(job => [job.id, job])));
      adopt(next); setVideo(asset);
      setWindows(old => [...history, ...old.filter(w => w.cursor < (history.at(-1)?.cursor ?? 0) && !history.some(item => item.id === w.id))]);
      setJobs(old => ({ ...old, ...Object.fromEntries(loaded.map(job => [job.id, job])) }));
      setConnected(true); setUpdated(Date.now()); if (!retry.current) setError(null);
      if (hydrated.current !== next.segment_id) {
        hydrated.current = next.segment_id;
        handlers.current.hydrate(next);
      }
      if (initialPosition.current) {
        // Reload cannot restore browser playback. Explicitly pause persisted admission.
        initialPosition.current = false;
        handlers.current.recover(next);
      }
    } catch (cause) {
      if (token === fence.current) { setConnected(false); setError(message(cause)); media.current?.pause(); initialPosition.current = true; }
    } finally { polling.current = false; }
  }

  useEffect(() => {
    const token = ++fence.current;
    void poll(token);
    const timer = window.setInterval(() => { setNow(Date.now()); void poll(fence.current); }, 1000);
    return () => {
      clearInterval(timer); fence.current++; media.current?.pause();
      const leaving = current.current;
      if (leaving?.id === id && leaving.state === "running") void commandSession(leaving.id, pauseCommand()).catch(() => {});
    };
  }, [id]); // Each selection invalidates every previous asynchronous response.

  useEffect(() => {
    // Closing the tab skips React cleanup; an orphaned running session would block every other session.
    function pauseOnExit() {
      const leaving = current.current;
      if (leaving?.state !== "running") return;
      void fetch(`/api/monitoring-sessions/${leaving.id}/commands`, {
        method: "POST", keepalive: true,
        headers: { ...CLIENT_HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify(pauseCommand()),
      }).catch(() => {});
    }
    window.addEventListener("pagehide", pauseOnExit);
    return () => window.removeEventListener("pagehide", pauseOnExit);
  }, []);

  async function older() {
    if (!session || !windows.length) return;
    const token = fence.current;
    try {
      const page = await getWindows(session.id, windows[windows.length - 1].cursor);
      const loaded = await Promise.all(page.flatMap(w => w.job_id ? [getJob(w.job_id)] : []));
      if (token !== fence.current) return;
      Object.assign(jobCache.current, Object.fromEntries(loaded.map(job => [job.id, job])));
      if (page.length < 100) setHistoryComplete(true);
      setWindows(old => [...old, ...page.filter(w => !old.some(existing => existing.id === w.id))]);
      setJobs(old => ({ ...old, ...Object.fromEntries(loaded.map(job => [job.id, job])) }));
    } catch (cause) { setError(message(cause)); }
  }

  return {
    id, select, sessions, session, video, windows, jobs, error, setError, connected, setConnected,
    updated, now, historyComplete, older, adopt, refresh: () => void poll(fence.current),
    media, fence, current, pending, positionPending, retry, initialPosition,
  };
}

export type MonitoringPoll = ReturnType<typeof useMonitoringPoll>;
