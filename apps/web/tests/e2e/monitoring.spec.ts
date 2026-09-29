import { expect, test } from "@playwright/test";

test("recorded sample lifecycle retains old generations and recovers after reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Monitoring", exact: true }).click();
  const panel = page.getByRole("region", { name: "Recorded monitoring" });
  await panel.getByRole("button", { name: "Use sample clip" }).click();
  await panel.getByRole("button", { name: "Create monitoring session" }).click();
  await expect(panel.getByText("Simulated predictions", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Start", exact: true }).click();
  await expect(panel.getByText(/System: running/)).toBeVisible();
  await expect(panel.locator("summary").filter({ hasText: /running/ })).toBeVisible({ timeout: 20000 });
  const seekIds: string[] = [];
  await page.route("**/api/monitoring-sessions/*/commands", async route => {
    const command = route.request().postDataJSON();
    if (command.action === "seek") {
      seekIds.push(command.command_id);
      if (seekIds.length === 1) { await route.fulfill({ status: 503, json: { detail: "temporary seek failure" } }); return; }
    }
    await route.continue();
  });
  await panel.getByLabel("Seek recording").fill("0.5");
  await expect(panel.getByRole("alert")).toContainText("temporary seek failure");
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await panel.getByRole("button", { name: "Retry connection / command" }).click();
  await expect(panel.getByText(/Current generation 1/)).toBeVisible();
  expect(seekIds).toHaveLength(2);
  expect(seekIds[1]).toBe(seekIds[0]);
  await expect(panel.getByText("No completed prediction in the current generation and segment.")).toBeVisible();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await panel.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(panel.getByText(/System: running/)).toBeVisible();
  await page.reload();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await expect(panel.locator("summary").filter({ hasText: /historical/ })).toBeVisible();
  await panel.getByRole("button", { name: "Resume", exact: true }).click();
  await panel.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(panel.getByText(/System: stopped/)).toBeVisible();
  await panel.getByRole("button", { name: "Restart / retry inference" }).click();
  await expect(panel.getByText(/Current generation 3/)).toBeVisible();
  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await panel.getByLabel("Monitoring frames").fill("8");
  await panel.getByRole("button", { name: "Apply new configuration segment" }).click();
  await expect(panel.getByLabel("Monitoring frames")).toHaveValue("8");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("button", { name: "Restart / retry inference" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByRole("button", { name: "Clip analysis", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Clip analysis", exact: true })).toBeVisible();
});

test("monitoring exposes timed failure, coverage gaps, disconnect and restart recovery separately from activity", async ({ page }) => {
  const config = { model: "fixture", prompt_preset: "baseline", prompt_text: "first part", backend_kind: "mock", fixture_version: "scripted-v1", preprocessing: { frames: 2, fps: 1, resize: 224, crop: "center" }, generation: { temperature: 0, max_tokens: 32 } };
  let session = { id: "session-fixture", video_id: "sample-fixture", state: "paused", generation: 0, segment_id: "segment-0", position: 4, duration: 6, stride: 2, expiration: 5, recovery_reason: null as string | null, latest_job_id: "job-1" as string | null, configuration: config };
  const jobRequests: Record<string, number> = {};
  let failure = false;
  let disconnected = false;
  const job = (id: string) => ({ id, video_id: session.video_id, configuration_id: "immutable-config", prepared_input_id: null, state: id === "job-failed" ? "failed" : "succeeded", start_seconds: 0, end_seconds: 2, attempt_count: 1, error: id === "job-failed" ? "Inference request timed out" : null, configuration: config, prediction: id === "job-failed" ? null : { label: ({ "job-1": "walk", "job-2": "fall", "job-3": "fallen" } as Record<string, string>)[id], sampled_timestamps: [0, 1, 2], backend_kind: "mock", fixture_version: "scripted-v1", request_duration_ms: 1234, total_duration_ms: 1300, completed_at: "2026-09-27T12:00:00Z" } });
  let releaseCapabilities!: () => void;
  const capabilitiesReady = new Promise<void>(resolve => { releaseCapabilities = resolve; });
  await page.route("**/api/capabilities", async route => { await capabilitiesReady; await route.continue(); });
  await page.addInitScript(() => { localStorage.setItem("sentinel-mode", "monitoring"); localStorage.setItem("sentinel-session", "session-fixture"); });
  await page.route("**/api/monitoring-sessions**", async route => {
    if (disconnected) { await route.fulfill({ status: 503, json: { detail: "service unavailable" } }); return; }
    const url = route.request().url();
    if (url.endsWith("commands")) {
      const command = route.request().postDataJSON();
      if (command.action === "restart") session = { ...session, generation: session.generation + 1, segment_id: "segment-new", latest_job_id: null, state: "running", position: 0 };
      if (command.action === "pause") session = { ...session, state: "paused" };
      if (command.action === "position") session = { ...session, position: command.position_seconds };
      await route.fulfill({ json: session });
    } else if (url.endsWith("windows")) {
      const count = failure ? 4 : Number(session.latest_job_id?.split("-").at(-1) ?? 3);
      const history = Array.from({ length: count }, (_, index) => ({ id: `window-${index}`, cursor: index + 2, generation: 0, sequence: index, sequence_end: index, segment_id: "segment-0", start_seconds: 0, end_seconds: 2, state: "submitted", reason: null, job_id: index === 3 ? "job-failed" : `job-${index + 1}` })).reverse();
      await route.fulfill({ json: [...history, { id: "gap", cursor: 1, generation: 0, sequence: 4, sequence_end: 7, segment_id: "segment-0", start_seconds: 2, end_seconds: 5, state: "skipped", reason: "superseded", job_id: null }] });
    }
    else await route.fulfill({ json: url.endsWith("session-fixture") ? session : [session] });
  });
  await page.route("**/api/videos/sample-fixture", route => route.fulfill({ json: { id: session.video_id, filename: "Scripted simulation", source: "synthetic", duration_seconds: 6 } }));
  await page.route("**/api/analysis-jobs/job-*", route => {
    const id = route.request().url().split("/").at(-1)!;
    jobRequests[id] = (jobRequests[id] ?? 0) + 1;
    return route.fulfill({ json: job(id) });
  });
  await page.goto("/");
  const panel = page.getByRole("region", { name: "Recorded monitoring" });
  await expect(panel.locator(".result-label").first()).toHaveText("walk");
  releaseCapabilities();
  await panel.locator("summary").filter({ hasText: /Prompt & generation/ }).click();
  await expect(panel.getByLabel("Resolved prompt")).toHaveValue("first part");
  await page.waitForTimeout(2200); // Observe multiple polls of the same terminal identity.
  expect(jobRequests["job-1"]).toBe(1);
  session = { ...session, latest_job_id: "job-2" };
  await expect(panel.locator(".result-label.alert").first()).toHaveText("fall");
  session = { ...session, latest_job_id: "job-3" };
  await expect(panel.locator(".result-label.alert").first()).toHaveText("fallen");
  expect(jobRequests["job-1"]).toBe(1);
  expect(jobRequests["job-2"]).toBe(1);
  await expect(panel.locator("summary").filter({ hasText: /Generation 0/ })).toHaveCount(4);
  await expect(panel.getByText(/Actual frame timestamps/).first()).toContainText("0.000 s, 1.000 s, 2.000 s");
  await expect(panel.locator("summary").filter({ hasText: /superseded/ })).toBeVisible();
  failure = true; session = { ...session, latest_job_id: null };
  await expect(panel.getByText("No completed prediction in the current generation and segment.")).toBeVisible();
  await panel.locator("summary").filter({ hasText: /failed/ }).click();
  await expect(panel.getByText(/System state: failed.*timed out/)).toBeVisible();
  disconnected = true;
  await expect(panel.getByText(/System: disconnected/)).toBeVisible();
  disconnected = false; session = { ...session, recovery_reason: "process_restart" };
  await expect(panel.getByText(/System: paused · process_restart/)).toBeVisible();
  await panel.getByRole("button", { name: "Restart / retry inference" }).click();
  await expect(panel.getByText(/Current generation 1/)).toBeVisible();
  await expect(panel.locator("summary").filter({ hasText: /historical/ }).first()).toBeVisible();
  await panel.getByRole("button", { name: "Pause", exact: true }).click();
});


test("real recording watermark follows media playback and recovers uploaded source", async ({ page }) => {
  const clip = process.env.FALL_DETECTION_E2E_CLIP!;
  await page.goto("/");
  await page.getByRole("button", { name: "Monitoring", exact: true }).click();
  const panel = page.getByRole("region", { name: "Recorded monitoring" });
  await panel.locator('input[type="file"]').setInputFiles(clip);
  await panel.getByLabel("Monitoring frames").fill("4");
  await panel.getByLabel("Monitoring FPS").fill("2");
  await panel.getByRole("button", { name: "Create monitoring session" }).click();
  await expect.poll(() => panel.locator("video").evaluate((video: HTMLVideoElement) => video.duration)).toBeGreaterThan(2);
  const watermark = page.waitForRequest(request => request.url().endsWith("commands") && request.postDataJSON().action === "position");
  await panel.getByRole("button", { name: "Start", exact: true }).click();
  const update = await watermark;
  const actual = await panel.locator("video").evaluate((video: HTMLVideoElement) => video.currentTime);
  expect(update.postDataJSON().position_seconds).toBeGreaterThan(0);
  expect(update.postDataJSON().position_seconds).toBeLessThanOrEqual(actual + 0.1);
  let pauseRequests = 0;
  page.on("request", request => { if (request.url().endsWith("commands") && request.postDataJSON().action === "pause") pauseRequests++; });
  await panel.locator("video").evaluate((video: HTMLVideoElement) => {
    const play = video.play.bind(video);
    let calls = 0;
    video.play = () => ++calls === 1 ? new Promise<void>((_, reject) => {
      (window as unknown as { rejectSeekPlayback: () => void }).rejectSeekPlayback = () => reject(new DOMException("interrupted", "AbortError"));
    }) : play();
  });
  await panel.getByLabel("Seek recording").fill("0.2");
  await expect(panel.getByText(/Current generation 1/)).toBeVisible();
  await expect.poll(() => page.evaluate(() => typeof (window as unknown as { rejectSeekPlayback?: () => void }).rejectSeekPlayback)).toBe("function");
  await panel.getByRole("button", { name: "Restart / retry inference" }).click();
  await expect(panel.getByText(/Current generation 2/)).toBeVisible();
  await page.evaluate(() => (window as unknown as { rejectSeekPlayback: () => void }).rejectSeekPlayback());
  await page.waitForTimeout(200); // Let the obsolete play rejection settle.
  expect(pauseRequests).toBe(0);
  await expect(panel.getByText(/System: running/)).toBeVisible();
  await expect.poll(async () => parseFloat((await panel.getByText(/^Playback \d/).innerText()).slice(9))).toBeGreaterThan(0);
  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await page.reload();
  await expect(panel.getByRole("heading", { name: "clip.mp4", exact: true })).toBeVisible();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await panel.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(panel.getByText(/System: running/)).toBeVisible();
  let outage = true;
  await page.route("**/api/monitoring-sessions", async route => {
    if (outage) await route.fulfill({ status: 503, json: { detail: "temporary outage" } });
    else await route.continue();
  });
  await expect(panel.getByText(/System: disconnected/)).toBeVisible();
  expect(await panel.locator("video").evaluate((video: HTMLVideoElement) => video.paused)).toBeTruthy();
  outage = false;
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await panel.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(panel.getByText(/Playback 3.000 s \/ 3.000 s/)).toBeVisible();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
});
