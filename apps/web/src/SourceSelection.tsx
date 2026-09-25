import { useRef } from "react";
import type { DatasetVideoOption, VideoAsset } from "./api";

type Props = {
  video: VideoAsset | null;
  previewUrl: string | null;
  duration: number | null;
  busy: boolean;
  datasetVideos: DatasetVideoOption[];
  selectedDatasetPath: string;
  showDatasetBrowser: boolean;
  onSample: () => void;
  onBrowseDataset: () => void;
  onDatasetPathChange: (path: string) => void;
  onOpenDataset: () => void;
  onUpload: (file: File) => void;
  onDurationLoaded: (duration: number) => void;
};

export function SourceSelection({
  video, previewUrl, duration, busy, datasetVideos, selectedDatasetPath,
  showDatasetBrowser, onSample, onBrowseDataset, onDatasetPathChange,
  onOpenDataset, onUpload, onDurationLoaded,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);

  return <>
    <div className="panel-heading">
      <div><span>01</span><h2>Source</h2></div>
      {video && <span className="asset-pill">{video.source === "synthetic" ? "Synthetic" : video.source === "dataset" ? "OmniFall" : "Uploaded"}</span>}
    </div>

    {video && <div className="source-caption"><strong>{video.filename}</strong><span>{duration !== null ? `${duration.toFixed(1)} s clip` : "Loading duration…"}</span></div>}

    <div className={`viewer ${video ? "loaded" : ""}`}>
      {!video && <div className="empty-state">
        <span className="upload-glyph">↗</span>
        <h3>Choose a clip</h3>
        <p>Use the built-in scenario now, or bring an MP4, MOV, WebM, or MKV.</p>
        <div className="source-actions">
          <button className="button primary" onClick={onSample} disabled={busy}>Use sample clip</button>
          <button className="button secondary" onClick={onBrowseDataset} disabled={busy}>Browse dataset</button>
          <button className="button secondary" onClick={() => fileInput.current?.click()} disabled={busy}>Upload video</button>
        </div>
        {showDatasetBrowser && <div className="dataset-browser">
          <label htmlFor="dataset-video">Prepared OmniFall video</label>
          <div>
            <select id="dataset-video" value={selectedDatasetPath} onChange={(event) => onDatasetPathChange(event.target.value)} disabled={busy || datasetVideos.length === 0}>
              {datasetVideos.map((option) => <option value={option.path} key={option.path}>{option.collection} · {option.subject} · {option.filename}</option>)}
            </select>
            <button className="button primary" onClick={onOpenDataset} disabled={busy || !selectedDatasetPath}>Open clip</button>
          </div>
          <small>{datasetVideos.length > 0 ? `${datasetVideos.length} local videos · folder names are dataset groupings, not predictions` : busy ? "Reading local catalog…" : "No videos available. Try a sample or upload a clip."}</small>
        </div>}
        <input ref={fileInput} type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska" hidden onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) onUpload(file);
        }} />
      </div>}
      {video?.source === "synthetic" && <div className="synthetic-scene" aria-label="Animated synthetic corridor preview">
        <div className="window-light" />
        <div className="corridor-line left" /><div className="corridor-line right" />
        <div className="figure"><span className="head" /><span className="body" /><span className="leg one" /><span className="leg two" /></div>
        <span className="scene-label">SYNTHETIC CORRIDOR · 00:06</span>
      </div>}
      {video && video.source !== "synthetic" && previewUrl && <video
        src={previewUrl}
        controls
        onLoadedMetadata={(event) => onDurationLoaded(event.currentTarget.duration)}
      />}
    </div>
  </>;
}
