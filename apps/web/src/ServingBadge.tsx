import { describeServing } from "./servingStatus";
import { useServingStatus } from "./useServingStatus";

/** Top-bar GPU state with an explicit warm-up; only the warm-up request ever wakes the GPU. */
export function ServingBadge() {
  const { status, error, warmUp } = useServingStatus(true);
  const view = error ? { text: "GPU status unavailable", tone: "alert" as const, title: error, canWarm: false } : describeServing(status);
  return <span className={`system-state serving-${view.tone}`} title={view.title} aria-live="polite">
    <i /> {view.text}
    {view.canWarm && <button type="button" className="warm-button" onClick={warmUp}>Warm up</button>}
  </span>;
}
