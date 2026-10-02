import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, postFrames } from "./api";
import type { CapturedFrame } from "./api";

export type CaptureStats = { frames: number; fps: number | null; lagSeconds: number | null; queued: number };

const BATCH_FRAMES = 32;
const UPLOAD_INTERVAL_MS = 250;
const MAX_SHORT_EDGE = 720;
const JPEG_QUALITY = 0.85;
// A backlog beyond this many seconds is abandoned; the next run records the gap explicitly.
const MAX_BACKLOG_SECONDS = 10;
const idle: CaptureStats = { frames: 0, fps: null, lagSeconds: null, queued: 0 };

function cameraMessage(cause: unknown) {
  if (cause instanceof DOMException && cause.name === "NotAllowedError") return "Camera permission was denied.";
  if (cause instanceof DOMException && cause.name === "NotFoundError") return "No camera was found.";
  return cause instanceof Error ? cause.message : "Camera unavailable";
}

function jpegBlob(canvas: HTMLCanvasElement): Blob {
  // Synchronous encoding keeps frames in capture order; run_seq must stay contiguous.
  const [, data] = canvas.toDataURL("image/jpeg", JPEG_QUALITY).split(",");
  const bytes = Uint8Array.from(atob(data), char => char.charCodeAt(0));
  return new Blob([bytes], { type: "image/jpeg" });
}

/**
 * Owns the camera stream and, while `running`, captures one capture run: frames are
 * numbered from 0 and timed from the run start, then uploaded in order. The server
 * places each run on the session timeline, so a new run per Start/Resume is safe.
 */
export function useLiveCapture(sessionId: string | null, running: boolean, captureFps: number, onStopped: (reason: string) => void) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<CaptureStats>(idle);
  const preview = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stopped = useRef(onStopped);
  stopped.current = onStopped;

  const attach = useCallback((element: HTMLVideoElement | null) => {
    preview.current = element;
    if (element && element.srcObject !== streamRef.current) element.srcObject = streamRef.current;
  }, []);

  function disconnect() {
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
    if (preview.current) preview.current.srcObject = null;
    setStream(null);
  }

  async function connect(): Promise<boolean> {
    if (streamRef.current) return true;
    setError(null);
    try {
      const next = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      next.getVideoTracks()[0]?.addEventListener("ended", () => { setError("Camera disconnected."); disconnect(); });
      streamRef.current = next;
      if (preview.current) preview.current.srcObject = next;
      setStream(next);
      return true;
    } catch (cause) {
      setError(cameraMessage(cause));
      return false;
    }
  }

  useEffect(() => () => { streamRef.current?.getTracks().forEach(track => track.stop()); }, []);

  useEffect(() => {
    if (!running || !stream || !sessionId) { setStats(idle); return; }
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    let runId = crypto.randomUUID();
    let start = performance.now();
    let nextSeq = 0;
    let lastCapture = -Infinity;
    let queue: CapturedFrame[] = [];
    let inFlight = false;
    let ended = false;
    let retryAt = 0;
    let backoff = 0;
    let uploaded = 0;

    function newRun() {
      runId = crypto.randomUUID();
      start = performance.now();
      nextSeq = 0;
      queue = [];
    }

    const capture = window.setInterval(() => {
      const video = preview.current;
      if (!context || !video || video.readyState < 2 || !video.videoWidth) return;
      // Coalesced timer callbacks must not exceed the server's twice-the-capture-rate bound.
      if (performance.now() - lastCapture < 750 / captureFps) return;
      lastCapture = performance.now();
      const scale = Math.min(1, MAX_SHORT_EDGE / Math.min(video.videoWidth, video.videoHeight));
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const captured = (performance.now() - start) / 1000;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      queue.push({ run_seq: nextSeq++, capture_seconds: captured, blob: jpegBlob(canvas) });
      if (queue.length > captureFps * MAX_BACKLOG_SECONDS && !inFlight) newRun();
    }, 1000 / captureFps);

    const upload = window.setInterval(() => {
      if (ended || inFlight || !queue.length || performance.now() < retryAt) return;
      const batch = queue.slice(0, BATCH_FRAMES);
      const batchRun = runId;
      inFlight = true;
      postFrames(sessionId, batchRun, batch).then(ack => {
        if (batchRun === runId) queue = queue.slice(batch.length);
        backoff = 0;
        uploaded += ack.accepted;
        const elapsed = (performance.now() - start) / 1000;
        setStats({ frames: uploaded, fps: elapsed > 1 ? nextSeq / elapsed : null, lagSeconds: Math.max(0, elapsed - batch[batch.length - 1].capture_seconds), queued: queue.length });
        if (ack.state !== "running") { ended = true; stopped.current(`Session ${ack.state}`); }
      }).catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 409 && /not running|superseded|maximum session length/.test(cause.message)) {
          ended = true;
          stopped.current(cause.message);
          return;
        }
        if (cause instanceof ApiError && cause.status === 409) { newRun(); return; } // Lost continuity; restart cleanly.
        backoff = Math.min(5000, backoff ? backoff * 2 : 500);
        retryAt = performance.now() + backoff;
        setStats(current => ({ ...current, queued: queue.length }));
      }).finally(() => { inFlight = false; });
    }, UPLOAD_INTERVAL_MS);

    return () => { ended = true; clearInterval(capture); clearInterval(upload); };
  }, [running, stream, sessionId, captureFps]);

  return { stream, error, stats, attach, connect, disconnect };
}
