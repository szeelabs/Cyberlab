import { defineConfig, devices } from "@playwright/test";

// Starts a real CyberLab control plane (development sign-in, fresh SQLite database) that
// serves the built web app, then drives it in Chromium. Build first: npm run build.
// E2E_DOCKER=1 also runs the full flow against a real Docker Engine.
const port = Number(process.env.E2E_PORT ?? 8799);
const dataDir = process.env.E2E_DATA_DIR ?? `/tmp/cyberlab-e2e-${process.pid}`;
const python = process.env.E2E_PYTHON ?? "python";

export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "off", ...devices["Desktop Chrome"] },
  webServer: {
    command: `rm -rf ${dataDir} && mkdir -p ${dataDir} && cd ../backend && ${python} -m uvicorn --factory cyberlab.main:app_factory --port ${port}`,
    url: `http://127.0.0.1:${port}/healthz`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: {
      CYBERLAB_ENVIRONMENT: "development",
      CYBERLAB_AUTH_MODE: "dev",
      CYBERLAB_DATABASE_URL: `sqlite:///${dataDir}/e2e.db`,
      CYBERLAB_SNAPSHOT_DIR: `${dataDir}/snapshots`,
      CYBERLAB_EXECUTION_PROVIDER: process.env.E2E_DOCKER === "1" ? "docker" : "none",
      CYBERLAB_IMAGE_MIRROR: process.env.E2E_IMAGE_MIRROR ?? "",
      CYBERLAB_REAPER_INTERVAL_SECONDS: "0",
      CYBERLAB_RATE_LIMIT_PER_MINUTE: "0",
    },
  },
});
