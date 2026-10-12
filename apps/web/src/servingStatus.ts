import type { ServingStatus } from "./api";

export type ServingView = { text: string; tone: "ok" | "busy" | "off" | "alert"; title: string; canWarm: boolean; canStop: boolean };

/** Describe GPU serving state; none of these states are read by contacting the GPU itself. */
export function describeServing(status: ServingStatus | null): ServingView {
  if (!status) return { text: "GPU status loading", tone: "off", title: "Waiting for the API", canWarm: false, canStop: false };
  const { state, warmup } = status;
  const seconds = warmup.seconds == null ? "" : ` (${Math.round(warmup.seconds)} s)`;
  const failed = warmup.state === "failed" ? ` Last warm-up failed: ${warmup.error ?? "unknown error"}.` : "";
  switch (state) {
    case "simulated":
      return { text: "Local simulation", tone: "ok", title: "Mock backend; no GPU is used", canWarm: false, canStop: false };
    case "warming":
      return { text: `GPU warming up${seconds}`, tone: "busy", title: "Waiting for vLLM to report healthy", canWarm: false, canStop: false };
    case "ready":
      return { text: "GPU ready", tone: "ok", title: `vLLM answered its health check${seconds}.`, canWarm: false, canStop: true };
    case "running":
      return { text: "GPU container up", tone: "busy", title: `A container is running (backlog ${status.backlog ?? 0}); it may still be starting.${failed}`, canWarm: true, canStop: true };
    case "idle":
      return { text: "GPU idle", tone: "off", title: `Scaled to zero; the next request waits for a cold start.${failed}`, canWarm: true, canStop: false };
    case "unmonitored":
      return { text: "Online vLLM", tone: "ok", title: `Container status is not configured (FALL_DETECTION_MODAL_APP).${failed}`, canWarm: true, canStop: false };
    case "unknown":
      return { text: "GPU status unknown", tone: "alert", title: `${status.detail ?? "Container status unavailable"}.${failed}`, canWarm: true, canStop: false };
  }
}
