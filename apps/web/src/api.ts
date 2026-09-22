export type VideoAsset = {
  id: string;
  filename: string;
  source: "upload" | "synthetic";
  storage_key: string | null;
  duration_seconds: number | null;
  created_at: string;
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
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "skipped";
  start_seconds: number;
  end_seconds: number;
  attempt_count: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  prediction: Prediction | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function createSample(): Promise<VideoAsset> {
  return request("/videos/sample", { method: "POST" });
}

export function uploadVideo(file: File): Promise<VideoAsset> {
  const body = new FormData();
  body.append("file", file);
  return request("/videos", { method: "POST", body });
}

export function createJob(videoId: string, startSeconds: number, endSeconds: number): Promise<AnalysisJob> {
  return request("/analysis-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: videoId,
      start_seconds: startSeconds,
      end_seconds: endSeconds,
    }),
  });
}

export function getJob(jobId: string): Promise<AnalysisJob> {
  return request(`/analysis-jobs/${jobId}`);
}
