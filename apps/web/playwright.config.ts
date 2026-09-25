import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const dataDir = process.env.FALL_DETECTION_E2E_DATA_DIR;
if (!dataDir) throw new Error("Run browser tests through scripts/run_browser_tests.py");

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: "http://localhost:5173",
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  webServer: {
    command: ".venv/bin/python scripts/dev.py",
    cwd: root,
    env: {
      FALL_DETECTION_DATA_DIR: dataDir,
      FALL_DETECTION_DATABASE_PATH: `${dataDir}/app.sqlite3`,
      FALL_DETECTION_BACKEND_KIND: "mock",
      FALL_DETECTION_INFERENCE_BASE_URL: "http://127.0.0.1:8001/v1",
      FALL_DETECTION_INFERENCE_MODEL: "qwen3-vl-8b-instruct",
      FALL_DETECTION_MOCK_FIXTURE_VERSION: "sample-v1",
      FALL_DETECTION_UPLOAD_MAX_BYTES: String(512 * 1024 * 1024),
      FALL_DETECTION_PREPARATION_SLOTS: "2",
      FALL_DETECTION_PREPARATION_WAIT_SECONDS: "10",
      MOCK_INFERENCE_MODEL: "qwen3-vl-8b-instruct",
      MOCK_INFERENCE_LABEL: "fall",
      MOCK_INFERENCE_DELAY_MS: "3500",
    },
    url: "http://localhost:5173",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
