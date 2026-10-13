import { expect, test } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

/** Sampling, model and prompt controls live in the settings dialog. */
async function editSettings(scope: Page | Locator, edit: () => Promise<void>, close = "Done") {
  await scope.getByRole("button", { name: "Settings", exact: true }).click();
  await edit();
  await scope.getByRole("button", { name: close, exact: true }).click();
}

test("live camera frames produce predictions and pause or reload leaves an explicit gap", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Monitoring", exact: true }).click();
  const panel = page.getByRole("region", { name: "Monitoring", exact: true });
  await panel.getByRole("button", { name: "Camera", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Create live session" })).toBeDisabled();
  await panel.getByRole("button", { name: "Connect camera" }).click();
  const feed = panel.locator("video.live-feed");
  await expect.poll(() => feed.evaluate((video: HTMLVideoElement) => video.videoWidth)).toBeGreaterThan(0);
  await editSettings(panel, async () => {
    await panel.getByLabel("Monitoring frames").fill("4");
    await panel.getByLabel("Monitoring FPS").fill("2");
  });
  await panel.getByRole("button", { name: "Create live session" }).click();
  await expect(panel.getByRole("heading", { name: "Live camera", exact: true })).toBeVisible();
  await expect(panel.getByLabel("Seek recording")).toHaveCount(0);

  await panel.getByRole("button", { name: "Start", exact: true }).click();
  await expect(panel.getByText(/System: running/)).toBeVisible();
  await expect(panel.getByText(/Camera: \d+\.\d fps/)).toBeVisible();
  const captured = async () => parseFloat((await panel.getByText(/^Live · \d+\.\d{3} s captured$/).innerText()).slice(7));
  const firstReading = await captured();
  // The clock follows the server watermark as frames arrive.
  await expect.poll(captured).toBeGreaterThan(firstReading + 1);
  // The mock labels every window "fall"; it must come from captured frames, with their timestamps.
  await expect(panel.locator(".result-label").first()).toHaveText("fall", { timeout: 30_000 });
  await expect(panel.getByText(/Actual frame timestamps/).first()).toContainText(" s");
  // The result is dated against the live edge, shows the frames the model saw and the work still in flight.
  const latest = panel.locator(".monitor-latest");
  await expect(latest.getByText(/^(Input ended|No current result · this one ended) \d+\.\d s ago$/)).toBeVisible();
  const seen = latest.getByRole("group", { name: "Frames the model saw" }).getByRole("img");
  await expect(seen.first()).toBeVisible();
  await expect.poll(() => seen.first().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(latest.getByRole("list", { name: "Windows in progress" })).toContainText("Filling the next 1.5 s window");

  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await page.reload();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await expect(panel.getByText("Camera: off")).toBeVisible();
  // Resume reconnects the camera and starts a new capture run behind an explicit gap.
  await panel.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(panel.getByText(/System: running/)).toBeVisible();
  // A slow first frame can add a second gap at the start of the capture, so require at least one.
  await expect(panel.locator("summary").filter({ hasText: /ingest_gap/ }).first()).toBeVisible();
  await expect(panel.getByText(/Camera: \d+\.\d fps/)).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
});
