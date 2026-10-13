import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { getServingStatus, shutDownServing, warmUpServing } from "./api.ts";
import type { ServingStatus } from "./api";

// Shutdown stops every model's GPU, so every badge sees one that is in flight.
let shutdownInFlight = false;
const shutdownListeners = new Set<() => void>();
function setShutdownInFlight(value: boolean) {
  shutdownInFlight = value;
  shutdownListeners.forEach((listener) => listener());
}
function subscribeShutdown(listener: () => void) {
  shutdownListeners.add(listener);
  return () => { shutdownListeners.delete(listener); };
}

/** Poll serving status from the API, which reads Modal's control plane and never wakes the GPU. */
export function useServingStatus(enabled: boolean, model?: string) {
  const [status, setStatus] = useState<ServingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [warmupInFlight, setWarmupInFlight] = useState(false);
  const shuttingDown = useSyncExternalStore(subscribeShutdown, () => shutdownInFlight);
  const changing = status?.state === "warming" || status?.state === "stopping";

  // Refreshes again whenever a shutdown starts or ends, so every badge follows it at once.
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
  }, [enabled, changing, model, shuttingDown]);

  // Shutdown blocks until Modal has stopped the containers; no second action meanwhile.
  const act = useCallback((setInFlight: (value: boolean) => void, action: () => Promise<ServingStatus>, fallback: string) => {
    setActionError(null);
    setInFlight(true);
    action().then((next) => { setStatus(next); setError(null); })
      .catch((cause) => setActionError(cause instanceof Error ? cause.message : fallback))
      .finally(() => setInFlight(false));
  }, []);
  const warmUp = useCallback(() => act(setWarmupInFlight, () => warmUpServing(model), "Warm-up failed"), [act, model]);
  const shutDown = useCallback(() => act(setShutdownInFlight, () => shutDownServing(model), "Shutdown failed"), [act, model]);
  const pending = shuttingDown ? "shutdown" : warmupInFlight ? "warmup" : null;

  return { status, error, actionError, pending, warmUp, shutDown };
}
