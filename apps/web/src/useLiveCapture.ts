import { useCallback, useEffect, useRef, useState } from "react";
import { postFrames } from "./api";
import { createCaptureUploader } from "./captureUploader";
import type { CaptureStats } from "./captureUploader";

export type { CaptureStats };

const UPLOAD_INTERVAL_MS = 250;
// The largest model input is 672 px; preparation only ever downscales from here.
const MAX_SHORT_EDGE = 672;
const JPEG_QUALITY = 0.85;
const idle: CaptureStats = { frames: 0, fps: null, lagSeconds: null, queued: 0 };

function cameraMessage(cause: unknown) {
  if (cause instanceof DOMException && cause.name === "NotAllowedError") return "Camera permission was denied.";
  if (cause instanceof DOMException && cause.name === "NotFoundError") return "No camera was found.";
  return cause instanceof Error ? cause.message : "Camera unavailable";
}

type FrameEncoder = { draw(video: HTMLVideoElement, width: number, height: number): void; encode(): Promise<Blob> };

/**
 * Draws frames to one reusable canvas and encodes them asynchronously. Both
 * `convertToBlob` and `toBlob` copy the bitmap when called, so the canvas can be
 * redrawn before encoding finishes. The synchronous data URL path is the fallback.
 */
function frameEncoder(): FrameEncoder | null {
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(1, 1);
    const context = canvas.getContext("2d");
    if (context) return {
      draw(video, width, height) {
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        context.drawImage(video, 0, 0, width, height);
      },
      encode: () => canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY }),
    };
  }
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return null;
  return {
    draw(video, width, height) {
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      context.drawImage(video, 0, 0, width, height);
    },
    encode() {
      if (typeof canvas.toBlob === "function") {
        return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("JPEG encoding failed")), "image/jpeg", JPEG_QUALITY));
      }
      try {
        const [, data] = canvas.toDataURL("image/jpeg", JPEG_QUALITY).split(",");
        return Promise.resolve(new Blob([Uint8Array.from(atob(data), char => char.charCodeAt(0))], { type: "image/jpeg" }));
      } catch (cause) {
        return Promise.reject(cause);
      }
    },
  };
}

/**
 * Owns the camera stream and, while `running`, captures one capture run: frames are
 * numbered from 0 and timed from the run start, then uploaded in order. The server
 * places each run on the session timeline, so a new run per Start/Resume is safe.
 */
export function useLiveCapture(sessionId: string | null, running: boolean, captureFps: number, onStopped: (reason: string) => void) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
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
    setUploadError(null);
    const encoder = frameEncoder();
    let lastCapture = -Infinity;
    const uploader = createCaptureUploader({
      captureFps,
      post: (runId, frames, signal) => postFrames(sessionId, runId, frames, signal),
      onStats: setStats,
      onEnded: (reason, failure) => {
        clearInterval(capture);
        clearInterval(upload);
        if (failure) setUploadError(failure);
        stopped.current(reason);
      },
    });

    const capture = window.setInterval(() => {
      const video = preview.current;
      if (!encoder || !video || video.readyState < 2 || !video.videoWidth) return;
      // Coalesced timer callbacks must not exceed the server's twice-the-capture-rate bound.
      if (performance.now() - lastCapture < 750 / captureFps) return;
      lastCapture = performance.now();
      const scale = Math.min(1, MAX_SHORT_EDGE / Math.min(video.videoWidth, video.videoHeight));
      encoder.draw(video, Math.round(video.videoWidth * scale), Math.round(video.videoHeight * scale));
      uploader.capture(encoder.encode());
    }, 1000 / captureFps);

    const upload = window.setInterval(() => uploader.pump(), UPLOAD_INTERVAL_MS);

    return () => { uploader.close(); clearInterval(capture); clearInterval(upload); };
  }, [running, stream, sessionId, captureFps]);

  return { stream, error: error ?? uploadError, stats, attach, connect, disconnect };
}
