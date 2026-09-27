import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import type { PreparedInput, VideoAsset } from "../src/api";
import { usePreparation } from "../src/usePreparation.ts";

const video = { id: "clip", source: "upload" } as VideoAsset;
const prepared = { id: "bundle", video_id: "clip", start_seconds: 0, end_seconds: 1,
  frame_count: 6, fps: 5, size: 224, frames: [] } as unknown as PreparedInput;
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json", "Retry-After": "3" },
});

function mount(fetcher: typeof fetch) {
  const dom = new JSDOM("<div id='root'></div>", { url: "http://localhost" });
  const previous = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, fetch: fetcher, IS_REACT_ACT_ENVIRONMENT: true });
  let state!: ReturnType<typeof usePreparation>;
  function Probe({ start }: { start: number }) {
    state = usePreparation(video, start, 6, 5, 224, true);
    return null;
  }
  const root = createRoot(dom.window.document.getElementById("root")!);
  return { get state() { return state; },
    async render(start = 0) { await act(async () => { root.render(React.createElement(Probe, { start })); }); },
    async wait() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 400)); }); },
    async unmount() { await act(async () => { root.unmount(); }); },
    async close() { await act(async () => { root.unmount(); }); dom.window.close(); Object.assign(globalThis, previous); },
  };
}

test("503 retry recovers identical selection and does not automatically retry invalid media", async () => {
  const bodies: unknown[] = [];
  const mounted = mount(async (_url, init) => {
    bodies.push(JSON.parse(String(init?.body)));
    return bodies.length === 1 ? response({ detail: "Preparation capacity is full" }, 503)
      : bodies.length === 2 ? response({ detail: "invalid media" }, 422) : response(prepared);
  });
  try {
    await mounted.render();
    await mounted.wait();
    assert.match(mounted.state.preparationError ?? "", /capacity/);
    assert.equal(mounted.state.visiblePrepared, null);
    await act(async () => { mounted.state.retryPreparation(); });
    await mounted.wait();
    assert.match(mounted.state.preparationError ?? "", /invalid media/);
    await mounted.wait();
    assert.equal(bodies.length, 2);
    await act(async () => { mounted.state.retryPreparation(); });
    await mounted.wait();
    assert.equal(mounted.state.visiblePrepared?.id, "bundle");
    assert.equal(mounted.state.preparationError, null);
    assert.deepEqual(bodies[0], bodies[2]);
  } finally { await mounted.close(); }
});

for (const change of ["selection", "unmount"] as const) {
  test(`pending retry result is ignored after ${change}`, async () => {
    let resolve!: (value: Response) => void;
    const pending = new Promise<Response>((done) => { resolve = done; });
    let attempts = 0;
    const mounted = mount(async () => {
      attempts += 1;
      if (attempts === 1) return response({ detail: "busy" }, 503);
      if (attempts === 2) return pending;
      return response({ ...prepared, id: "current", start_seconds: 1, end_seconds: 2 });
    });
    try {
      await mounted.render();
      await mounted.wait();
      await act(async () => { mounted.state.retryPreparation(); });
      await mounted.wait();
      if (change === "selection") {
        await mounted.render(1);
        await mounted.wait();
        assert.equal(mounted.state.visiblePrepared?.id, "current");
      } else await mounted.unmount();
      await act(async () => { resolve(response(prepared)); await pending; });
      assert.equal(mounted.state.visiblePrepared?.id, change === "selection" ? "current" : undefined);
    } finally { await mounted.close(); }
  });
}

test("selection change cancels a retry still waiting for debounce", async () => {
  const starts: number[] = [];
  const mounted = mount(async (_url, init) => {
    const start = JSON.parse(String(init?.body)).start_seconds;
    starts.push(start);
    return start === 0 ? response({ detail: "busy" }, 503)
      : response({ ...prepared, start_seconds: 1, end_seconds: 2 });
  });
  try {
    await mounted.render();
    await mounted.wait();
    await act(async () => { mounted.state.retryPreparation(); });
    await mounted.render(1);
    await mounted.wait();
    assert.deepEqual(starts, [0, 1]);
    assert.equal(mounted.state.visiblePrepared?.start_seconds, 1);
  } finally { await mounted.close(); }
});
