import { useEffect, useState } from "react";
import { createPreparedInput } from "./api";
import type { PreparedInput, VideoAsset } from "./api";

export function usePreparation(video: VideoAsset | null, start: number, count: number, fps: number, size: number, valid: boolean) {
  const [prepared, setPrepared] = useState<PreparedInput | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = start + (count - 1) / fps;

  useEffect(() => {
    setPrepared(null);
    setError(null);
    if (!video || video.source === "synthetic" || !valid) {
      setPreparing(false);
      return;
    }
    let cancelled = false;
    setPreparing(true);
    const timer = window.setTimeout(async () => {
      try {
        const next = await createPreparedInput(video.id, start, count, fps, size);
        if (!cancelled) setPrepared(next);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Frame preparation failed");
      } finally {
        if (!cancelled) setPreparing(false);
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [video, start, count, fps, size, valid]);

  const matches = Boolean(prepared && video && prepared.video_id === video.id &&
    Math.abs(prepared.start_seconds - start) < 0.001 &&
    Math.abs(prepared.end_seconds - end) < 0.001 &&
    prepared.frame_count === count && Math.abs(prepared.fps - fps) < 0.000001 && prepared.size === size);
  return { visiblePrepared: matches ? prepared : null, preparing, preparationError: error };
}
