export type Tone = "done" | "alert" | "active" | "gap" | "failed" | "idle";

// Paint order when windows overlap: a fall must never be hidden under a later, less important window.
const paintOrder: Tone[] = ["gap", "idle", "active", "done", "failed", "alert"];

export function inPaintOrder<T extends { tone: Tone; start: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => paintOrder.indexOf(a.tone) - paintOrder.indexOf(b.tone) || a.start - b.start);
}
