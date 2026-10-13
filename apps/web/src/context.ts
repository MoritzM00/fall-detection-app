import type { Capabilities } from "./api";

/**
 * Conservative upper bound of the tokens one request needs on the served Qwen3-VL model.
 * Mirrors `estimate_request_tokens` in `src/fall_detection/models.py`; the API enforces it.
 */
export function estimateRequestTokens(frameCount: number, size: number, promptText: string, maxTokens: number): number {
  const side = Math.max(Math.ceil(size / 32), 12);
  const visual = Math.ceil(frameCount / 2) * (side * side + 16);
  return visual + new TextEncoder().encode(promptText).length + 32 + maxTokens;
}

/** Estimated request size against the served context window, or null when no window is reported. */
export function contextBudget(
  capabilities: Pick<Capabilities, "serving_limits"> | null,
  frameCount: number, size: number, promptText: string, maxTokens: number,
): { estimate: number; limit: number } | null {
  const limit = capabilities?.serving_limits?.max_model_len;
  if (limit == null || !Number.isFinite(maxTokens)) return null;
  return { estimate: estimateRequestTokens(frameCount, size, promptText, maxTokens), limit };
}

/** Why a configuration cannot fit the served context window, or null when it can or no window is reported. */
export function contextWindowError(
  capabilities: Pick<Capabilities, "serving_limits"> | null,
  frameCount: number, size: number, promptText: string, maxTokens: number,
): string | null {
  const limit = capabilities?.serving_limits?.max_model_len;
  if (limit == null) return null;
  const estimate = estimateRequestTokens(frameCount, size, promptText, maxTokens);
  if (estimate <= limit) return null;
  return `This configuration may need up to ${estimate.toLocaleString("en-US")} tokens, but the served model's context window is ${limit.toLocaleString("en-US")}. Use fewer frames, a smaller crop, a shorter prompt or fewer max tokens.`;
}
