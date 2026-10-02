import { expect, test } from "@playwright/test";

test("live camera frames produce predictions and pause or reload leaves an explicit gap", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Monitoring", exact: true }).click();
  const panel = page.getByRole("region", { name: "Monitoring", exact: true });
  await panel.getByRole("button", { name: "Camera", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Create live session" })).toBeDisabled();
  await panel.getByRole("button", { name: "Connect camera" }).click();
  const feed = panel.locator("video.live-feed");
  await expect.poll(() => feed.evaluate((video: HTMLVideoElement) => video.videoWidth)).toBeGreaterThan(0);
  await panel.getByLabel("Monitoring frames").fill("4");
  await panel.getByLabel("Monitoring FPS").fill("2");
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

  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await page.reload();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
  await expect(panel.getByText("Camera: off")).toBeVisible();
  // Resume reconnects the camera and starts a new capture run behind an explicit gap.
  await panel.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(panel.getByText(/System: running/)).toBeVisible();
  await expect(panel.locator("summary").filter({ hasText: /ingest_gap/ })).toBeVisible();
  await expect(panel.getByText(/Camera: \d+\.\d fps/)).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(panel.getByText(/System: paused/)).toBeVisible();
});
