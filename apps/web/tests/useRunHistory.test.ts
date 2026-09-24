import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import type { AnalysisJob } from "../src/api";
import { useRunHistory } from "../src/useRunHistory.ts";

function job(id: string, state: AnalysisJob["state"]): AnalysisJob {
  return { id, state, video_id: id, created_at: `2026-09-24T10:00:0${id === "active" ? 0 : 1}Z`,
    updated_at: "2026-09-24T10:00:00Z" } as AnalysisJob;
}

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function mount(fetcher: typeof fetch) {
  const dom = new JSDOM("<div id='root'></div>", { url: "http://localhost" });
  const previous = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, fetch: fetcher, IS_REACT_ACT_ENVIRONMENT: true });
  let state!: ReturnType<typeof useRunHistory>;
  function Probe() {
    state = useRunHistory();
    return React.createElement("div", { id: "status" }, `${state.ready}:${state.activeJob?.id ?? "none"}:${state.selectedJob?.id ?? "none"}:${state.pollError ?? "ok"}`);
  }
  const root = createRoot(dom.window.document.getElementById("root")!);
  return { root, dom, get state() { return state; },
    async start() { await act(async () => { root.render(React.createElement(Probe)); }); },
    async close() { await act(async () => { root.unmount(); }); dom.window.close(); Object.assign(globalThis, previous); },
  };
}

test("delayed history cannot erase a newly recorded run or its selection", async () => {
  const history = deferred<Response>();
  const mounted = mount(async () => history.promise);
  try {
    await mounted.start();
    assert.equal(mounted.state.ready, false);
    await act(async () => { mounted.state.record(job("active", "queued")); });
    await act(async () => { history.resolve(response([])); await history.promise; });
    assert.equal(mounted.state.activeJob?.id, "active");
    assert.equal(mounted.state.selectedJob?.id, "active");
    assert.equal(mounted.state.ready, true);
  } finally { await mounted.close(); }
});

test("selecting a completed run keeps another run tracked and polling errors visible", async () => {
  const mounted = mount(async (url) => String(url).includes("/analysis-jobs/")
    ? response({ detail: "offline" }, 503) : response([job("active", "running"), job("old", "succeeded")]));
  try {
    await mounted.start();
    assert.equal(mounted.state.activeJob?.id, "active");
    await act(async () => { mounted.state.setSelectedJobId("old"); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 550)); });
    assert.equal(mounted.state.selectedJob?.id, "old");
    assert.equal(mounted.state.activeJob?.id, "active");
    assert.match(mounted.state.pollError ?? "", /offline/);
  } finally { await mounted.close(); }
});

test("failed history recovery keeps submission readiness false", async () => {
  const mounted = mount(async () => response({ detail: "unavailable" }, 503));
  try {
    await mounted.start();
    assert.equal(mounted.state.ready, false);
    assert.match(mounted.state.pollError ?? "", /unavailable/);
  } finally { await mounted.close(); }
});
