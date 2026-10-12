import { useEffect, useRef, useState } from "react";
import { commandSession } from "./api";
import type { MonitoringCommand, MonitoringSession } from "./api";
import { message } from "./useMonitoringPoll";
import type { MonitoringPoll } from "./useMonitoringPoll";

const PLAYBACK_BLOCKED = "Playback was blocked. Press Resume to try again.";
const pauseCommand = (): MonitoringCommand => ({ action: "pause", command_id: crypto.randomUUID() });
const starts = (action: MonitoringCommand["action"]) => ["start", "resume", "restart"].includes(action);

/**
 * Sends one lifecycle command at a time and reports the playback position.
 *
 * Position reports share the current fence so polling continues; every other
 * command advances it. A failed command is kept for an explicit retry.
 */
export function useSessionCommands(poll: MonitoringPoll) {
  const { media, fence, current, pending, positionPending, retry, initialPosition, adopt, setError, setConnected } = poll;
  const [busy, setBusy] = useState(false);
  const [position, setPosition] = useState(0);
  const syntheticPosition = useRef(0);

  async function send(command: MonitoringCommand, isRetry = false, preserveRetry = false) {
    const saved = current.current;
    if (!saved) return;
    while (positionPending.current) await new Promise(resolve => window.setTimeout(resolve, 20));
    if (current.current?.id !== saved.id) return;
    if (pending.current) return;
    const payload = isRetry && retry.current ? retry.current.command : { ...command, command_id: crypto.randomUUID() };
    const target = isRetry && retry.current ? retry.current.id : saved.id;
    const token = payload.action === "position" ? fence.current : ++fence.current;
    positionPending.current = payload.action === "position";
    pending.current = true; if (!positionPending.current) setBusy(true); if (!preserveRetry) setError(null);
    if (!preserveRetry) retry.current = { id: target, command: payload };
    try {
      const next = await commandSession(target, payload);
      if (token !== fence.current) {
        if (starts(payload.action) && next.state === "running") void commandSession(target, pauseCommand()).catch(() => {});
        return;
      }
      if (payload.action === "position" && (current.current?.generation !== next.generation || current.current.segment_id !== next.segment_id)) return;
      if (!preserveRetry) retry.current = null; current.current = next; adopt(next); setConnected(true);
      if (payload.action !== "position") {
        syntheticPosition.current = next.position; setPosition(next.position);
        if (media.current && Math.abs(media.current.currentTime - next.position) > 0.1) media.current.currentTime = next.position;
      }
      if (starts(payload.action) && media.current) {
        try { await media.current.play(); } catch {
          if (token !== fence.current) return;
          setError(PLAYBACK_BLOCKED);
          const paused = await commandSession(target, pauseCommand());
          if (token === fence.current) adopt(paused);
        }
      }
      return next;
    } catch (cause) { if (token === fence.current) { setError(message(cause)); setConnected(false); media.current?.pause(); initialPosition.current = true; } }
    finally { pending.current = false; positionPending.current = false; setBusy(false); }
  }

  /** Restart playback from a recovered session; a session left running is paused. */
  function recover(next: MonitoringSession) {
    syntheticPosition.current = next.position; setPosition(next.position);
    if (next.state === "running") void send({ action: "pause" }, false, true);
  }

  /** Seek, then resume the media element if the session is still running the same timeline. */
  function seek(seconds: number) {
    media.current?.pause();
    void send({ action: "seek", position_seconds: seconds }).then(next => {
      const saved = current.current;
      if (!next || saved?.id !== next.id || saved.generation !== next.generation || saved.segment_id !== next.segment_id || saved.state !== "running") return;
      const playbackFence = fence.current;
      void media.current?.play().catch(() => {
        if (playbackFence === fence.current) { setError(PLAYBACK_BLOCKED); void send({ action: "pause" }); }
      });
    });
  }

  const synthetic = poll.video?.source === "synthetic";
  useEffect(() => {
    let last = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const saved = current.current;
      if (saved?.state === "running" && poll.connected && !pending.current && !initialPosition.current && !retry.current) {
        const actual = synthetic ? Math.min(saved.duration, syntheticPosition.current + (now - last) / 1000) : media.current?.currentTime;
        if (actual !== undefined && Number.isFinite(actual)) {
          syntheticPosition.current = actual; setPosition(actual);
          if (actual >= saved.position) void send({ action: "position", position_seconds: Math.min(actual, saved.duration) });
        }
      }
      last = now;
    }, 500);
    return () => clearInterval(timer);
  }, [poll.video?.source, poll.connected]);

  return { busy, setBusy, position, send, recover, seek };
}
