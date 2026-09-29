import assert from "node:assert/strict";
import test from "node:test";
import { inPaintOrder } from "../src/coverage.ts";

test("overlapping fall windows are painted above later windows of any other state", () => {
  const painted = inPaintOrder([
    { tone: "alert", start: 0 },
    { tone: "done", start: 1 },
    { tone: "active", start: 2 },
    { tone: "gap", start: 1.5 },
    { tone: "failed", start: 0.5 },
    { tone: "alert", start: 3 },
  ] as const);
  assert.deepEqual(painted.map(item => `${item.tone}@${item.start}`), ["gap@1.5", "active@2", "done@1", "failed@0.5", "alert@0", "alert@3"]);
});
