import assert from "node:assert/strict";
import test from "node:test";
import type { ServingStatus } from "../src/api";
import { describeServing } from "../src/servingStatus.ts";

const status = (overrides: Partial<ServingStatus>): ServingStatus => ({
  state: "idle", containers: 0, backlog: 0, detail: null, warmup: { state: "idle", seconds: null, error: null }, ...overrides,
});

test("serving view offers warm-up when not known ready and shutdown when a container is up", () => {
  assert.deepEqual(
    ["simulated", "warming", "ready", "running", "idle", "unmonitored", "unknown"].map((state) => describeServing(status({ state: state as ServingStatus["state"] })).canWarm),
    [false, false, false, true, true, true, true],
  );
  assert.deepEqual(
    ["simulated", "warming", "ready", "running", "idle", "unmonitored", "unknown"].map((state) => describeServing(status({ state: state as ServingStatus["state"] })).canStop),
    [false, false, true, true, false, false, false],
  );
  assert.equal(describeServing(null).text, "GPU status loading");
});

test("serving view shows warm-up progress and failures", () => {
  assert.equal(describeServing(status({ state: "warming", warmup: { state: "warming", seconds: 41.6, error: null } })).text, "GPU warming up (42 s)");
  const failed = describeServing(status({ warmup: { state: "failed", seconds: null, error: "PermissionError: rejected (401)" } }));
  assert.equal(failed.text, "GPU idle");
  assert.match(failed.title, /Last warm-up failed: PermissionError: rejected \(401\)/);
});
