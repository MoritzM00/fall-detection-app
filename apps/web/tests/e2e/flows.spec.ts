import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

const clip = process.env.FALL_DETECTION_E2E_CLIP;
if (!clip) throw new Error("Run browser tests through scripts/run_browser_tests.py");

async function waitForApi(request: APIRequestContext) {
  await expect.poll(async () => (await request.get("http://127.0.0.1:8000/health")).status()).toBe(200);
}

test("uploaded frames and submitted result survive source and sampling changes", async ({ page, request }) => {
  await waitForApi(request);
  await page.goto("/");
  await expect(page.getByText("Local simulation")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(clip);
  await expect(page.locator(".source-caption")).toContainText("clip.mp4");
  await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.duration)).toBeGreaterThan(2.5);
  await expect(page.locator(".source-caption")).toContainText("3.0 s clip");

  await page.getByLabel("Frames", { exact: true }).fill("8");
  await page.getByLabel("Sampling FPS").fill("4");
  await page.getByLabel("Crop size").selectOption("224");
  await expect(page.locator(".sample-frame img")).toHaveCount(8);
  await expect.poll(() => page.locator(".sample-frame img").first().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(224);
  await expect(page.locator(".frame-strip figcaption").last()).toHaveText("1.750s");

  await page.locator(".analyze-button").click();
  await expect(page.getByText(/Submitted source: clip.mp4 · 0.000–1.750 s/)).toBeVisible();
  await page.getByRole("button", { name: "Change source" }).click();
  await page.getByRole("button", { name: "Use sample clip" }).click();
  await page.getByLabel("Frames", { exact: true }).fill("4");
  await expect(page.getByText(/Submitted source: clip.mp4 · 0.000–1.750 s/)).toBeVisible();
  await expect(page.locator(".result-card .result-label")).toHaveText("fall", { timeout: 20_000 });
  await page.reload();
  await expect(page.getByText(/Submitted source: clip.mp4 · 0.000–1.750 s/)).toBeVisible();
  await expect(page.locator(".result-card .result-label")).toHaveText("fall");
});



test("sample, upload, and dataset sources reset editable sampling through one path", async ({ page, request }) => {
  await waitForApi(request);
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(clip);
  await expect(page.locator(".source-caption")).toContainText("clip.mp4");
  await page.getByLabel("Frames", { exact: true }).fill("8");
  await page.getByLabel("Sampling FPS").fill("4");
  await page.getByLabel("Crop size").selectOption("224");
  await page.getByRole("button", { name: "Change source" }).click();
  await page.getByRole("button", { name: "Use sample clip" }).click();
  await expect(page.getByLabel("Frames", { exact: true })).toHaveValue("16");
  await expect(page.getByLabel("Sampling FPS")).toHaveValue("7.5");
  await expect(page.getByLabel("Crop size")).toHaveValue("448");
  await page.getByRole("button", { name: "Change source" }).click();
  await page.getByRole("button", { name: "Browse dataset" }).click();
  await expect(page.locator("#dataset-video option")).toHaveCount(1);
  await page.getByRole("button", { name: "Open clip" }).click();
  await expect(page.locator(".asset-pill")).toHaveText("OmniFall");
  await expect(page.getByLabel("Frames", { exact: true })).toHaveValue("16");
  await page.getByLabel("Frames", { exact: true }).fill("8");
  await page.getByRole("button", { name: "Change source" }).click();
  await page.locator('input[type="file"]').setInputFiles(clip);
  await expect(page.locator(".asset-pill")).toHaveText("Uploaded");
  await expect(page.getByLabel("Frames", { exact: true })).toHaveValue("16");
});

test("recovers history and protects an active run after reload", async ({ page, request }) => {
  await waitForApi(request);
  await page.goto("/");
  await page.getByRole("button", { name: "Use sample clip" }).click();
  const firstResponse = page.waitForResponse((response) => response.url().endsWith("/api/analysis-jobs") && response.request().method() === "POST");
  await page.locator(".analyze-button").click();
  const firstJob = await (await firstResponse).json();
  await expect(page.locator(".result-card .result-label")).toHaveText("fall", { timeout: 20_000 });

  const secondResponse = page.waitForResponse((response) => response.url().endsWith("/api/analysis-jobs") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Run again" }).click();
  const secondJob = await (await secondResponse).json();
  await page.route(`**/api/analysis-jobs/${secondJob.id}`, async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "temporary poll failure" }) });
  });
  await page.reload();
  await expect(page.getByRole("button", { name: /Queued|Analyzing/ })).toBeDisabled();
  await expect(page.getByText(/Run status unavailable: temporary poll failure/)).toBeVisible();
  await page.locator("#recent-run").selectOption(firstJob.id);
  await expect(page.locator(".result-card .result-label")).toHaveText("fall");
  await expect(page.getByText(/Submitted source: Synthetic corridor clip/)).toBeVisible();
  await page.unroute(`**/api/analysis-jobs/${secondJob.id}`);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText(/Run status unavailable/)).toHaveCount(0);
});

test("shows history errors and recovers before allowing submission", async ({ page, request }) => {
  await waitForApi(request);
  await page.route("**/api/analysis-jobs?limit=50", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "history unavailable" }) });
  });
  await page.goto("/");
  await expect(page.getByText(/Run status unavailable: history unavailable/)).toBeVisible();
  await page.getByRole("button", { name: "Use sample clip" }).click();
  await expect(page.locator(".analyze-button")).toBeDisabled();
  await page.unroute("**/api/analysis-jobs?limit=50");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText(/Run status unavailable/)).toHaveCount(0);
  await expect(page.locator(".analyze-button")).toBeEnabled();
});

test("renders failed and retryable frame preparation", async ({ page, request }) => {
  await waitForApi(request);
  let attempts = 0;
  await page.route("**/api/prepared-inputs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts += 1;
    if (attempts === 1) {
      return route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "decode failed" }) });
    }
    if (attempts === 2) {
      return route.fulfill({ status: 503, headers: { "Retry-After": "3" }, contentType: "application/json", body: JSON.stringify({ detail: "Preparation capacity is full; retry shortly" }) });
    }
    return route.continue();
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(clip);
  await expect(page.getByRole("alert").filter({ hasText: "decode failed" })).toBeVisible();
  await page.getByLabel("Sampling FPS").fill("6");
  await expect(page.getByRole("alert").filter({ hasText: "Preparation capacity is full" })).toBeVisible();
  await page.getByLabel("Sampling FPS").fill("8");
  await expect(page.locator(".sample-frame img")).toHaveCount(16);
  await expect(page.locator(".analyze-button")).toBeEnabled();
  expect(attempts).toBe(3);
});


test("retries a failed submitted job after its prepared input is restored", async ({ page, request }) => {
  await waitForApi(request);
  const dataDir = process.env.FALL_DETECTION_E2E_DATA_DIR;
  if (!dataDir) throw new Error("Missing disposable browser test data directory");
  await page.goto("/");
  const preparedResponse = page.waitForResponse((response) => response.url().endsWith("/api/prepared-inputs") && response.request().method() === "POST");
  await page.locator('input[type="file"]').setInputFiles(clip);
  const prepared = await (await preparedResponse).json();
  await expect(page.locator(".sample-frame img")).toHaveCount(16);
  const framePath = join(dataDir, "prepared", prepared.id, "00.jpg");
  const originalFrame = readFileSync(framePath);
  let jobId = "";
  try {
    writeFileSync(framePath, "damaged frame");
    const submission = page.waitForResponse((response) => response.url().endsWith("/api/analysis-jobs") && response.request().method() === "POST");
    await page.locator(".analyze-button").click();
    jobId = (await (await submission).json()).id;
    await expect(page.locator(".result-card.failure")).toContainText("integrity check", { timeout: 20_000 });
  } finally {
    writeFileSync(framePath, originalFrame);
  }
  await page.route("**/api/analysis-jobs?limit=50", async (route) => {
    const response = await route.fetch();
    const jobs = await response.json();
    await route.fulfill({ response, body: JSON.stringify(jobs.map((job: { id: string }) => job.id === jobId
      ? { ...job, error: "Worker interrupted before claim ownership was recorded. Retry this run." }
      : job)) });
  });
  await page.reload();
  await expect(page.locator(".result-card.failure")).toContainText("Worker interrupted");
  await page.unroute("**/api/analysis-jobs?limit=50");
  await page.getByRole("button", { name: "Retry run" }).click();
  await expect(page.locator(".result-card .result-label")).toHaveText("fall", { timeout: 20_000 });
});
