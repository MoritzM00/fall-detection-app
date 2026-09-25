import { useMemo, useState } from "react";
import { fpsForEnd, submittedEnd } from "./runState";

export function useSamplingSelection() {
  const [startSeconds, setStartSeconds] = useState(0);
  const [frameCount, setFrameCount] = useState(16);
  const [fps, setFps] = useState(7.5);
  const [size, setSize] = useState(448);
  const endSeconds = submittedEnd(startSeconds, frameCount, fps);
  const rangeDuration = endSeconds - startSeconds;
  const isBaselineWindow = Math.abs(rangeDuration - 2) < 0.001;
  const previewTimestamps = useMemo(() => {
    if (endSeconds <= startSeconds) return [];
    const step = rangeDuration / (frameCount - 1);
    return Array.from({ length: frameCount }, (_, index) => startSeconds + step * index);
  }, [startSeconds, endSeconds, rangeDuration, frameCount]);

  function reset() {
    setStartSeconds(0);
    setFrameCount(16);
    setFps(7.5);
    setSize(448);
  }

  function fitDuration(duration: number) {
    if (duration < endSeconds && duration > startSeconds) {
      setFps((frameCount - 1) / (duration - startSeconds));
    }
  }

  function updateRange(nextStart: number, nextEnd: number) {
    setStartSeconds(nextStart);
    const nextFps = fpsForEnd(nextStart, nextEnd, frameCount);
    if (nextFps !== null) setFps(nextFps);
  }

  function isValid(duration: number | null) {
    return startSeconds >= 0 && endSeconds > startSeconds && rangeDuration <= 30 &&
      fps > 0 && fps <= 30 && frameCount >= 2 && frameCount <= 32 &&
      (duration === null || endSeconds <= duration + 0.01);
  }

  return {
    startSeconds, endSeconds, frameCount, fps, size, rangeDuration, isBaselineWindow,
    previewTimestamps, setFrameCount, setFps, setSize, reset, fitDuration, updateRange, isValid,
  };
}
