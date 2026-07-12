import { defineConfig, devices } from '@playwright/test';

/*
 * SPA end-to-end harness — Layer 2 of the verification system (notes/verify/).
 * Drives the real SPA in the running container across the matrix axes a UI
 * change can break (notes/verify/MATRIX.md): viewport (desktop + mobile),
 * default-state, and — via the E2E_SUBPATH rig — reverse-proxy sub-path.
 *
 * Theme behavior and rendered light/dark colors are covered by theme.spec.ts;
 * broad cross-route theme/viewport sweeps remain part of the live visual gate.
 *
 * Server is expected already-running:
 *   local:  cwn-local           at http://localhost:8086   (E2E_BASE_URL default)
 *   subpath: cwn-nginx-571 rig   at http://localhost:8087   (E2E_SUBPATH_URL)
 *   CI:     the job builds the image, runs the container, then invokes this.
 */

const BASE_URL = process.env.E2E_BASE_URL || 'http://localhost:8086';
const STORAGE = 'e2e/.auth/state.json';
const isCI = !!process.env.CI;

export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.results',
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: isCI ? 2 : undefined,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: isCI
    ? [['list'], ['html', { outputFolder: 'e2e/.report', open: 'never' }], ['github']]
    : [['list'], ['html', { outputFolder: 'e2e/.report', open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: isCI ? 'on-first-retry' : 'off',
  },
  projects: [
    // 1. Log in once; every authed project reuses the saved session.
    { name: 'setup', testMatch: /global\.setup\.ts/ },

    // 2. Desktop — the full user flow + a11y (mobile-only specs excluded).
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 }, storageState: STORAGE },
      dependencies: ['setup'],
      testIgnore: [/subpath\.spec\.ts/, /mobile\.spec\.ts/],
    },

    // 3. Mobile 375×667 (chromium mobile emulation — no webkit dep) — where every
    //    mobile regression (#288/#576/#1411) shipped.
    {
      name: 'mobile',
      use: {
        browserName: 'chromium',
        viewport: { width: 375, height: 667 },
        isMobile: true,
        hasTouch: true,
        storageState: STORAGE,
      },
      dependencies: ['setup'],
      testIgnore: /subpath\.spec\.ts/,
    },

    // 4. Sub-path reverse proxy (opt-in: set E2E_SUBPATH_URL to the nginx rig).
    //    Guards Class 1 subpath breakage (v4.1.1 reader 404, #571 white page).
    ...(process.env.E2E_SUBPATH_URL
      ? [{
          name: 'subpath',
          testMatch: /subpath\.spec\.ts/,
          use: { ...devices['Desktop Chrome'], baseURL: process.env.E2E_SUBPATH_URL, storageState: STORAGE },
          dependencies: ['setup'],
        }]
      : []),
  ],
});
