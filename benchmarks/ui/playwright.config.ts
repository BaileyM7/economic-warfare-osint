import { defineConfig, devices } from '@playwright/test';

// Standalone Playwright config for the Ask Anything before/after capture.
// Target + creds come from env so the same spec runs against local or live.
//
//   BASE_URL=https://…onrender.com EMISSARY_USER=demo EMISSARY_PASS=demo123 \
//     BENCH_LABEL=after npx playwright test
//
// A cold analysis is ~6–7 min, so timeouts are generous.
export default defineConfig({
  testDir: '.',
  timeout: 15 * 60 * 1000, // 15 min per test — a cold swarm run can take ~7 min
  expect: { timeout: 15 * 60 * 1000 },
  retries: 0,
  workers: 1, // serial — analyses are expensive; don't run them in parallel
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  outputDir: 'test-results',
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:8000',
    ignoreHTTPSErrors: true,
    screenshot: 'only-on-failure',
    video: 'on', // the "before/after picture" of the real service running
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
