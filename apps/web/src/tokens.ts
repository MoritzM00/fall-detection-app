import type { Prediction } from "./api";

/** Server-reported token usage; older runs and backends without usage show as not reported. */
export function formatTokenUsage(prediction: Pick<Prediction, "prompt_tokens" | "completion_tokens">): string {
  const { prompt_tokens: prompt, completion_tokens: completion } = prediction;
  if (prompt == null && completion == null) return "Not reported";
  const count = (value: number | null | undefined) => (value == null ? "?" : value.toLocaleString("en-US"));
  return `${count(prompt)} prompt · ${count(completion)} completion`;
}
