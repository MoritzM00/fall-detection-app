import { useCallback, useEffect, useState } from "react";
import { getServingStatus, warmUpServing } from "./api.ts";
import type { ServingStatus } from "./api";

/** Poll serving status from the API, which reads Modal's control plane and never wakes the GPU. */
export function useServingStatus(enabled: boolean) {
  const [status, setStatus] = useState<ServingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const warming = status?.state === "warming";

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const refresh = () => {
      if (document.hidden) return;
      getServingStatus()
        .then((next) => { if (!cancelled) { setStatus(next); setError(null); } })
        .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : "Status unavailable"); });
    };
    refresh();
    const timer = window.setInterval(refresh, warming ? 3_000 : 15_000);
    document.addEventListener("visibilitychange", refresh);
    return () => { cancelled = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [enabled, warming]);

  const warmUp = useCallback(() => {
    warmUpServing().then((next) => { setStatus(next); setError(null); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Warm-up failed"));
  }, []);

  return { status, error, warmUp };
}
