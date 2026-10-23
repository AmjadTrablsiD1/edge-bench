import { defineConfig, devices } from "@playwright/test";

// The suite drives the *shipped* app: FastAPI serving the built ui/dist on one
// port, which is what the launcher starts -- not the Vite dev server.
const PORT = 8793;
export default defineConfig({
  testDir: "tests/e2e",
  outputDir: "test-results",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1440, height: 900 }, trace: "retain-on-failure" },
  projects: [
    { name: "midnight", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, colorScheme: "dark" } },
    { name: "daylight", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, colorScheme: "light" } },
  ],
  webServer: {
    command: `.venv/bin/python main.py --no-browser --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/whoami`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
