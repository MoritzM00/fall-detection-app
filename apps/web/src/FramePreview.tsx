import type { PreparedInput, VideoAsset } from "./api";

type Props = {
  video: VideoAsset;
  frameCount: number;
  timestamps: number[];
  prepared: PreparedInput | null;
  preparing: boolean;
  error: string | null;
  valid: boolean;
};

export function FramePreview({ video, frameCount, timestamps, prepared, preparing, error, valid }: Props) {
  const displayed = prepared ? prepared.frames.map((frame) => frame.actual_seconds) : timestamps;
  return <section className="frame-preview" aria-labelledby="frame-preview-title">
    <div className="frame-preview-heading">
      <div><span className="control-label">Frames for analysis</span><strong id="frame-preview-title">{frameCount} timestamped frames</strong></div>
      <span className="preview-disclaimer">{video.source === "synthetic" ? "Synthetic preview" : prepared ? "Frames sent to vLLM" : "Preparing frames…"}</span>
    </div>
    {preparing && <p className="preview-note" role="status">Decoding the selected video window…</p>}
    {error && <p className="range-error" role="alert">{error}</p>}
    {valid && (video.source === "synthetic" || prepared) && <div className="frame-strip">
      {displayed.map((timestamp, index) => <figure className="sample-frame" key={`${timestamp}-${index}`}>
        <div className="frame-image">
          {prepared ? <img src={`/api/prepared-inputs/${prepared.id}/frames/${index}`} alt={`Prepared frame ${index + 1}`} /> :
            <div className="synthetic-frame"><i className="mini-window" /><i className="mini-figure" style={{ transform: `rotate(${Math.min(78, (index / (frameCount - 1)) * 88)}deg)` }} /></div>}
          <span>{String(index + 1).padStart(2, "0")}</span>
        </div>
        <figcaption>{timestamp.toFixed(3)}s</figcaption>
      </figure>)}
    </div>}
    <p className="preview-note">{prepared ? "These JPEG previews are the encoded frames in the online video request. The activity label is simulated when using the mock backend." : "Change the window or settings to prepare a new frame bundle."}</p>
  </section>;
}
