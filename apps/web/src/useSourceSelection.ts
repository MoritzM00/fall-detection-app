import { useEffect, useState } from "react";
import { createDatasetVideo, createSample, listDatasetVideos, uploadVideo } from "./api";
import type { DatasetVideoOption, VideoAsset } from "./api";

export function useSourceSelection(resetSampling: () => void, fitDuration: (duration: number) => void) {
  const [video, setVideo] = useState<VideoAsset | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [datasetVideos, setDatasetVideos] = useState<DatasetVideoOption[]>([]);
  const [selectedDatasetPath, setSelectedDatasetPath] = useState("");
  const [showDatasetBrowser, setShowDatasetBrowser] = useState(false);

  function selectSource(nextVideo: VideoAsset | null, url: string | null) {
    setVideo(nextVideo);
    setPreviewUrl(url);
    setDuration(nextVideo?.duration_seconds ?? null);
    setShowDatasetBrowser(false);
    resetSampling();
  }

  useEffect(() => {
    return () => {
      if (previewUrl?.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  async function useSample() {
    setBusy(true);
    setError(null);
    try {
      selectSource(await createSample(), null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create sample");
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const asset = await uploadVideo(file);
      selectSource(asset, URL.createObjectURL(file));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function browseDataset() {
    setError(null);
    setShowDatasetBrowser(true);
    if (datasetVideos.length > 0) return;
    setBusy(true);
    try {
      const options = await listDatasetVideos();
      setDatasetVideos(options);
      setSelectedDatasetPath(options[0]?.path ?? "");
      if (options.length === 0) setError("No prepared dataset videos were found");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not read the dataset catalog");
    } finally {
      setBusy(false);
    }
  }

  async function useDatasetVideo() {
    if (!selectedDatasetPath) return;
    setBusy(true);
    setError(null);
    try {
      const asset = await createDatasetVideo(selectedDatasetPath);
      selectSource(asset, `/api/videos/${asset.id}/media`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not open the dataset video");
    } finally {
      setBusy(false);
    }
  }

  function onDurationLoaded(nextDuration: number) {
    if (!Number.isFinite(nextDuration)) return;
    setDuration(nextDuration);
    fitDuration(nextDuration);
  }

  return {
    video, previewUrl, duration, busy, error, datasetVideos, selectedDatasetPath,
    showDatasetBrowser, setSelectedDatasetPath, setError, useSample, upload,
    browseDataset, useDatasetVideo, onDurationLoaded, clear: () => selectSource(null, null),
  };
}
