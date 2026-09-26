export type VideoAsset = {
  id: string;
  filename: string;
  source: "upload" | "synthetic" | "dataset";
  storage_key: string | null;
  duration_seconds: number | null;
  created_at: string;
};

export type DatasetVideoOption = {
  path: string;
  dataset: string;
  subject: string;
  collection: string;
  filename: string;
};

export type Prediction = {
  id: string;
  label: string;
  raw_response: string;
  sampled_timestamps: number[];
  backend_kind: string;
  model: string;
  fixture_version: string | null;
  request_duration_ms: number;
  total_duration_ms: number;
  completed_at: string;
};

export type AnalysisJob = {
  id: string;
  video_id: string;
  configuration_id: string;
  prepared_input_id: string | null;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "skipped";
  start_seconds: number;
  end_seconds: number;
  attempt_count: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  configuration: { model: string; prompt_preset: string; prompt_text: string; backend_kind: string; fixture_version: string | null; preprocessing: { frames: number; fps: number; resize: number; crop: string; bundle_sha256?: string | null }; generation: GenerationSettings } | null;
  prediction: Prediction | null;
};

export type PreparedInput = {
  id: string;
  video_id: string;
  start_seconds: number;
  end_seconds: number;
  frame_count: number;
  fps: number;
  size: number;
  bundle_sha256: string;
  frames: { index: number; requested_seconds: number; actual_seconds: number; sha256: string }[];
};

export type GenerationSettings = { temperature: number; max_tokens: number };
export type ExperimentSettings = { model: string; prompt_text: string; generation: GenerationSettings };
export type Capabilities = { backend_kind: string; simulated: boolean; models: string[]; prompt_preset: { id: string; prompt: string }; generation: GenerationSettings; generation_limits: { min_max_tokens: number; max_max_tokens: number } };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item) => typeof item?.msg === "string" ? item.msg : "").filter(Boolean).join("; ")
      : "";
    throw new Error(message || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function createSample(): Promise<VideoAsset> {
  return request("/videos/sample", { method: "POST" });
}

export function getCapabilities(): Promise<Capabilities> {
  return request("/capabilities");
}

export function listDatasetVideos(): Promise<DatasetVideoOption[]> {
  return request("/dataset-videos");
}

export function createDatasetVideo(path: string): Promise<VideoAsset> {
  return request("/videos/dataset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export function uploadVideo(file: File): Promise<VideoAsset> {
  const body = new FormData();
  body.append("file", file);
  return request("/videos", { method: "POST", body });
}

export function createPreparedInput(videoId: string, startSeconds: number, frameCount: number, fps: number, size: number): Promise<PreparedInput> {
  return request("/prepared-inputs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_id: videoId, start_seconds: startSeconds, frame_count: frameCount, fps, size }),
  });
}

export function createJob(videoId: string, startSeconds: number, endSeconds: number, preparedInputId: string | null, frameCount = 16, fps = 7.5, size = 448, experiment?: ExperimentSettings): Promise<AnalysisJob> {
  return request("/analysis-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: videoId,
      start_seconds: startSeconds,
      end_seconds: endSeconds,
      prepared_input_id: preparedInputId,
      frame_count: frameCount,
      fps,
      size,
      ...experiment,
    }),
  });
}

export function getJob(jobId: string): Promise<AnalysisJob> {
  return request(`/analysis-jobs/${jobId}`);
}

export function listJobs(): Promise<AnalysisJob[]> {
  return request("/analysis-jobs?limit=50");
}

export function retryJob(jobId: string): Promise<AnalysisJob> {
  return request(`/analysis-jobs/${jobId}/retry`, { method: "POST" });
}

export function getVideo(videoId: string): Promise<VideoAsset> {
  return request(`/videos/${videoId}`);
}
