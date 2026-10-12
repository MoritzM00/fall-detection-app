import assert from "node:assert/strict";
import test from "node:test";
import { contextWindowError, estimateRequestTokens } from "../src/context.ts";

// Same values as estimate_request_tokens in src/fall_detection/models.py.
test("token estimate matches the server's conservative bound", () => {
  assert.equal(estimateRequestTokens(16, 448, "", 0), 1728);
  assert.equal(estimateRequestTokens(16, 672, "", 0), 3688);
  assert.equal(estimateRequestTokens(32, 672, "", 0), 7344);
  assert.equal(estimateRequestTokens(3, 224, "ä", 100), 2 * 160 + 2 + 32 + 100);
});

test("context window errors only when a reported window is exceeded", () => {
  const deployed = { serving_limits: { max_model_len: 5120 } };
  const prompt = "x".repeat(407); // thesis prompt length in bytes
  assert.equal(contextWindowError(deployed, 16, 448, prompt, 32), null);
  assert.equal(contextWindowError(deployed, 16, 672, prompt, 32), null);
  assert.match(contextWindowError(deployed, 32, 672, prompt, 32) ?? "", /up to 7,783 tokens.*context window is 5,120/);
  assert.match(contextWindowError(deployed, 16, 448, prompt, 4096) ?? "", /context window/);
  assert.equal(contextWindowError({ serving_limits: { max_model_len: null } }, 32, 672, prompt, 4096), null);
  assert.equal(contextWindowError({}, 32, 672, prompt, 4096), null);
  assert.equal(contextWindowError(null, 32, 672, prompt, 4096), null);
});
