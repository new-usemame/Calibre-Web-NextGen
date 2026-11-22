import { defineConfig } from '@playwright/test';

// Exercise the actual picker components without mutating a shared library.
export default defineConfig({
  testDir: './component-tests',
  retries: 0,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:12434', browserName: 'chromium' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 12434 --strictPort',
    url: 'http://127.0.0.1:12434/static/app/',
    reuseExistingServer: !process.env.CI,
  },
});
