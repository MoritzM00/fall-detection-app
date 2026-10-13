import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import { useServingStatus } from "../src/useServingStatus.ts";

const running = { state: "running", containers: 1, backlog: 0, detail: null, warmup: { state: "idle", seconds: null, error: null } };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("a shutdown greys out every model's badge and releases them when it is refused", async () => {
  let refuse!: (value: Response) => void;
  const shutdown = new Promise<Response>((done) => { refuse = done; });
  const reads: string[] = [];
  const dom = new JSDOM("<div id='root'></div>", { url: "http://localhost", pretendToBeVisual: true });
  const previous = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  Object.assign(globalThis, {
    window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true,
    fetch: async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return shutdown;
      reads.push(url);
      return response(running);
    },
  });
  const states: Record<string, ReturnType<typeof useServingStatus>> = {};
  function Probe({ model }: { model: string }) { states[model] = useServingStatus(true, model); return null; }
  const root = createRoot(dom.window.document.getElementById("root")!);
  try {
    await act(async () => { root.render([React.createElement(Probe, { key: "a", model: "a" }), React.createElement(Probe, { key: "b", model: "b" })]); });
    assert.equal(states.b.pending, null);
    const readsBefore = reads.length;
    await act(async () => { states.a.shutDown(); });
    assert.equal(states.a.pending, "shutdown");
    assert.equal(states.b.pending, "shutdown");
    assert.ok(reads.slice(readsBefore).some((url) => url.endsWith("model=b")), "the other badge refreshes at once");
    await act(async () => { refuse(response({ detail: "1 request(s) in flight" }, 409)); await shutdown; });
    assert.equal(states.a.pending, null);
    assert.equal(states.b.pending, null);
    assert.match(states.a.actionError ?? "", /in flight/);
  } finally {
    await act(async () => { root.unmount(); });
    dom.window.close();
    Object.assign(globalThis, previous);
  }
});
