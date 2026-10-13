import { useCallback, useEffect, useState } from "react";
import { getServingStatus, shutDownServing, warmUpServing } from "./api.ts";
import type { ServingStatus } from "./api";

/** Poll serving status from the API, which reads Modal's control plane and never wakes the GPU. */
export function useServingStatus(enabled: boolean, model?: string) {
  const [status, setStatus] = useState<ServingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<"warmup" | "shutdown" | null>(null);
  const changing = status?.state === "warming" || status?.state === "stopping";

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const refresh = () => {
      if (document.hidden) return;
      getServingStatus(model)
        .then((next) => { if (!cancelled) { setStatus(next); setError(null); } })
        .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : "Status unavailable"); });
    };
    refresh();
    const timer = window.setInterval(refresh, changing ? 3_000 : 15_000);
    document.addEventListener("visibilitychange", refresh);
    return () => { cancelled = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [enabled, changing, model]);

  // Shutdown blocks until Modal has stopped the containers; no second action meanwhile.
  const act = useCallback((kind: "warmup" | "shutdown", action: () => Promise<ServingStatus>, fallback: string) => {
    setActionError(null);
    setPending(kind);
    action().then((next) => { setStatus(next); setError(null); })
      .catch((cause) => setActionError(cause instanceof Error ? cause.message : fallback))
      .finally(() => setPending(null));
  }, []);
  const warmUp = useCallback(() => act("warmup", () => warmUpServing(model), "Warm-up failed"), [act, model]);
  const shutDown = useCallback(() => act("shutdown", () => shutDownServing(model), "Shutdown failed"), [act, model]);

  return { status, error, actionError, pending, warmUp, shutDown };
}
