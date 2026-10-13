import { useState } from "react";
import type { AnalysisJob } from "./api";
import { comparisonRows, samePreparedFrames, sameWindow } from "./comparisonState";
import { runName } from "./runState";

export function RunComparison({ jobs, selectedJob }: { jobs: AnalysisJob[]; selectedJob: AnalysisJob | null }) {
  const [comparisonId, setComparisonId] = useState("");
  if (!selectedJob) return null;
  const candidates = jobs.filter((item) => item.id !== selectedJob.id && sameWindow(item, selectedJob));
  // Only runs of the same source and window are comparable; hide the section until one exists.
  if (!candidates.length) return null;
  const other = candidates.find((item) => item.id === comparisonId);
  return <section className="run-comparison" aria-label="Run comparison">
    <div className="comparison-heading">
      <div><h2>Compare runs</h2><p className="preview-note">Other runs of this source and window. Differences are highlighted.</p></div>
      <div className="comparison-picker">
        <label htmlFor="compare-run">Compare with</label>
        <select id="compare-run" value={other?.id ?? ""} onChange={(event) => setComparisonId(event.target.value)}>
          <option value="">Choose a saved run</option>
          {candidates.map((item) => <option key={item.id} value={item.id}>{runName(item)}</option>)}
        </select>
      </div>
    </div>
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
