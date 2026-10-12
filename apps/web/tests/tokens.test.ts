import assert from "node:assert/strict";
import test from "node:test";
import { formatTokenUsage } from "../src/tokens.ts";

test("token usage shows reported counts and marks missing usage explicitly", () => {
  assert.equal(formatTokenUsage({ prompt_tokens: 1700, completion_tokens: 6 }), "1,700 prompt · 6 completion");
  assert.equal(formatTokenUsage({ prompt_tokens: 1700, completion_tokens: null }), "1,700 prompt · ? completion");
  assert.equal(formatTokenUsage({ prompt_tokens: null, completion_tokens: null }), "Not reported");
  assert.equal(formatTokenUsage({}), "Not reported");
});
