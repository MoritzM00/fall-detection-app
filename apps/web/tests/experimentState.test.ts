import assert from "node:assert/strict";
import test from "node:test";
import type { Capabilities } from "../src/api.ts";
import { baselineExperiment, matchingPreset, modelLabel, presetTitle, withModel } from "../src/experimentState.ts";

const generation = { temperature: 0, max_tokens: 64, top_p: 0.8, top_k: 20, seed: 0 };
const capabilities = {
  labels: [], backend_kind: "vllm", simulated: false, generation_limits: { min_max_tokens: 16, max_max_tokens: 4096 },
  models: ["qwen3-vl-8b-instruct", "qwen3-vl-8b-thinking"], thinking_models: ["qwen3-vl-8b-thinking"],
  prompt_presets: [
    { id: "zs", title: "Zero-shot", description: "", prompt: "Answer.", chain_of_thought: false, generation },
    { id: "cot", title: "Chain of thought", description: "", prompt: "Reason, then answer.", chain_of_thought: true, generation: { ...generation, temperature: 0.5, max_tokens: 4096 } },
  ],
} satisfies Capabilities;

test("choosing a thinking model turns the unchanged zero-shot preset into chain of thought", () => {
  const thinking = withModel(capabilities, baselineExperiment(capabilities), "qwen3-vl-8b-thinking");
  assert.equal(thinking.model, "qwen3-vl-8b-thinking");
  assert.equal(thinking.chain_of_thought, true);
  assert.equal(thinking.generation.max_tokens, 4096);
  assert.equal(matchingPreset(capabilities, thinking)?.id, "cot");
  assert.equal(presetTitle(capabilities, matchingPreset(capabilities, thinking)!, thinking.model), "Chain of thought · Thinking");
  // Edited settings and the way back keep everything but the model.
  const edited = { ...baselineExperiment(capabilities), prompt_text: "Custom." };
  assert.deepEqual(withModel(capabilities, edited, "qwen3-vl-8b-thinking"), { ...edited, model: "qwen3-vl-8b-thinking" });
  assert.deepEqual(withModel(capabilities, thinking, "qwen3-vl-8b-instruct"), { ...thinking, model: "qwen3-vl-8b-instruct" });
});

test("model labels keep the distinguishing last part of the served name", () => {
  assert.equal(modelLabel("qwen3-vl-8b-thinking"), "Thinking");
  assert.equal(modelLabel("Qwen/Qwen3-VL-8B-Instruct"), "Instruct");
  assert.equal(presetTitle(capabilities, capabilities.prompt_presets[0], "qwen3-vl-8b-instruct"), "Zero-shot");
});
