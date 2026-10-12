import { describeServing } from "./servingStatus";
import { useServingStatus } from "./useServingStatus";

/** Top-bar GPU state with explicit warm-up and shutdown; status reads never wake the GPU. */
export function ServingBadge() {
  const { status, error, actionError, warmUp, shutDown } = useServingStatus(true);
  const view = error
    ? { text: "GPU status unavailable", tone: "alert" as const, title: error, canWarm: false, canStop: false }
    : describeServing(status);
  return <span className={`system-state serving-${view.tone}`} title={view.title} aria-live="polite">
    <i /> {view.text}
    {view.canWarm && <button type="button" className="warm-button" onClick={warmUp}>Warm up</button>}
    {view.canStop && <button type="button" className="warm-button" onClick={shutDown}>Shut down</button>}
    {actionError && <span className="serving-error" role="alert">{actionError}</span>}
  </span>;
}
