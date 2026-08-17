import { test, expect, type Request } from '@playwright/test';

const isApiRequest = (request: Request) => request.url().includes('/api/');

const isTaskQueuePoll = (request: Request) =>
  new URL(request.url()).pathname.endsWith('/api/v1/tasks');

test('returning to the tab does not refetch cached SPA queries', async ({ page }) => {
  const inFlightApi = new Set<Request>();
  page.on('request', (request) => {
    if (isApiRequest(request)) inFlightApi.add(request);
  });
  page.on('requestfinished', (request) => inFlightApi.delete(request));
  page.on('requestfailed', (request) => inFlightApi.delete(request));

  // The desktop project supplies the same authenticated storage state used by
  // browse.spec.ts; /app is the library/browse route exercised there.
  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
  await page.waitForLoadState('networkidle');
  await expect.poll(() => inFlightApi.size, {
    message: 'the SPA must be idle before measuring focus-triggered traffic',
    timeout: 10_000,
  }).toBe(0);

  const focusCycleRequests: string[] = [];
  const countRequest = (request: Request) => {
    if (!isApiRequest(request)) return;
    // The task queue intentionally polls every four seconds. It can fire during
    // this window independently of focus, so only that endpoint is excluded.
    if (isTaskQueuePoll(request)) return;
    focusCycleRequests.push(request.url());
  };
  page.on('request', countRequest);

  // This Playwright version has no Emulation.setPageVisibilityState CDP command.
  // Dispatch from document with bubbling so React Query v5's window-level
  // visibilitychange listener receives the same hidden -> visible transition.
  await page.evaluate(async () => {
    const setVisibility = (state: 'hidden' | 'visible') => {
      Object.defineProperty(document, 'hidden', {
        configurable: true,
        value: state === 'hidden',
      });
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        value: state,
      });
      document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
    };

    setVisibility('hidden');
    await new Promise((resolve) => setTimeout(resolve, 50));
    setVisibility('visible');
    window.dispatchEvent(new Event('focus'));
  });

  await page.waitForTimeout(2_000);
  page.off('request', countRequest);

  expect(
    focusCycleRequests,
    `focus cycle generated ${focusCycleRequests.length} non-polling /api/ requests`,
  ).toHaveLength(0);
});
