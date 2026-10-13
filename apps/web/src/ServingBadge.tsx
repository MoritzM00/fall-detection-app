import { modelLabel } from "./experimentState";
import { describeServing } from "./servingStatus";
import { useServingStatus } from "./useServingStatus";

/**
 * Top-bar GPU state of one served model with explicit warm-up and shutdown; status reads
 * never wake the GPU. With several models, each has its own badge and GPU.
 */
export function ServingBadge({ model, labelled = false }: { model?: string; labelled?: boolean }) {
  const { status, error, actionError, pending, warmUp, shutDown } = useServingStatus(true, model);
  const view = error
    ? { text: "GPU status unavailable", tone: "alert" as const, title: error, canWarm: false, canStop: false }
    : describeServing(status);
  const prefix = labelled && model ? `${modelLabel(model)}: ` : "";
  return <span className={`system-state serving-${view.tone}`} title={`${model ? `${model}: ` : ""}${view.title}`} aria-live="polite">
    <i /> {prefix}{view.text}
    {view.canWarm && <button type="button" className="warm-button" disabled={pending !== null} onClick={warmUp}>{pending === "warmup" ? "Warming…" : "Warm up"}</button>}
    {view.canStop && <button type="button" className="warm-button" disabled={pending !== null} onClick={shutDown} title={labelled ? "Stops every model's GPU" : undefined}>{pending === "shutdown" ? "Shutting down…" : "Shut down"}</button>}
    {actionError && <span className="serving-error" role="alert">{actionError}</span>}
  </span>;
}
