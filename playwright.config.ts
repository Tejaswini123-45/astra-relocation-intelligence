import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against a real API and a real Next.js build.
 *
 * Nothing is stubbed. The point of these tests is to prove that what a judge
 * clicks is backed by a computation, so a mocked API would test the opposite of
 * what matters. Start both services before running:
 *
 *     uvicorn astra.main:app --port 8000        (from apps/api)
 *     npm run build:web && npm run start -w @astra/web
 *
 * or let the web server block below start the frontend for you.
 */
const WEB = process.env.ASTRA_WEB_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  // A what-if re-runs the whole chain over a 100 m grid. That is seconds, not
  // milliseconds, and a tight timeout here would make a slow but correct
  // computation look like a broken screen.
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The map is a GPU surface in a headless browser. A fixed viewport keeps the
    // three-column layout in its wide arrangement so selectors are stable.
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: process.env.ASTRA_WEB_URL
    ? undefined
    : {
        command: "npm run start --workspace @astra/web",
        url: WEB,
        reuseExistingServer: true,
        timeout: 180_000,
      },
});
