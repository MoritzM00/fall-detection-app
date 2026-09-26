import { useState } from "react";
import type { AnalysisJob } from "./api";
import { comparisonRows, samePreparedFrames, sameWindow } from "./comparisonState";

export function RunComparison({ jobs, selectedJob }: { jobs: AnalysisJob[]; selectedJob: AnalysisJob | null }) {
  const [comparisonId, setComparisonId] = useState("");
  if (!selectedJob) return null;
  const candidates = jobs.filter((item) => item.id !== selectedJob.id && sameWindow(item, selectedJob));
  const other = candidates.find((item) => item.id === comparisonId);
  return <section className="run-comparison" aria-label="Run comparison">
    <h2>Compare runs</h2>
    <p className="preview-note">Compare the selected saved run with another run of the same source and time window. Differences are highlighted. Recent history includes up to 50 finished runs.</p>
    <label htmlFor="compare-run">Compare with</label>
    <select id="compare-run" value={other?.id ?? ""} disabled={!candidates.length} onChange={(event) => setComparisonId(event.target.value)}>
      <option value="">{candidates.length ? "Choose a saved run" : "No other run for this source and window"}</option>
      {candidates.map((item) => <option key={item.id} value={item.id}>{item.created_at.slice(0, 19)} · {item.state} · {item.id}</option>)}
    </select>
    {other && <>
      <p className="preview-note">{samePreparedFrames(selectedJob, other) ? "Both runs use the same immutable prepared frames." : "Input frames differ or cannot be verified as identical. Compare sampling and input identity as well as model settings."}</p>
      {(selectedJob.configuration?.backend_kind === "mock" || other.configuration?.backend_kind === "mock") && <p className="simulation-note">Mock results and timings are simulated; this comparison does not measure model quality or GPU performance.</p>}
      <div className="comparison-scroll"><table>
        <caption>Saved runs for {selectedJob.start_seconds.toFixed(3)}–{selectedJob.end_seconds.toFixed(3)} s</caption>
        <thead><tr><th scope="col">Field</th><th scope="col">Selected run <small>{selectedJob.id}</small></th><th scope="col">Comparison run <small>{other.id}</small></th></tr></thead>
        <tbody>{comparisonRows(selectedJob, other).map((row) => <tr key={row.name} className={row.changed ? "changed" : undefined}><th scope="row">{row.name}{row.changed && <small>Different</small>}</th><td>{row.left}</td><td>{row.right}</td></tr>)}</tbody>
      </table></div>
    </>}
  </section>;
}
