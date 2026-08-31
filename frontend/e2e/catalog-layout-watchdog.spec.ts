import { test, expect } from '@playwright/test';
import {
  CATALOG_LAYOUT_HEAL_MS,
  hostileLoadProfile,
  installCatalogLayoutWatchdog,
  installHostileLoad,
  readCatalogLayoutWatchdog,
  type HostileLoadEvent,
} from './catalog-layout-watchdog';

const VIEWPORTS = [768, 1_280, 1_440];

test.beforeEach(async ({ page }, testInfo) => {
  const profile = hostileLoadProfile(testInfo);
  const events: HostileLoadEvent[] = [];
  if (profile) await installHostileLoad(page, profile, events);
  await installCatalogLayoutWatchdog(page);
  (testInfo as typeof testInfo & { hostileLoadEvents?: HostileLoadEvent[] }).hostileLoadEvents = events;
});

for (const width of VIEWPORTS) {
  test(`catalog grid converges to its arithmetic track count at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/app');

    const grid = page.getByTestId('catalog-grid');
    await expect(grid).toBeVisible();
    await expect(grid.locator('[data-virtual-grid-row]').first()).toBeVisible();
    await page.evaluate(() => document.fonts?.ready);

    // Let the convergence contract fully elapse. A still-active episode must be
    // red at settle time; an episode that healed inside the budget stays in the
    // evidence log but is tolerated.
    await page.waitForTimeout(CATALOG_LAYOUT_HEAL_MS + 100);
    const snapshot = await readCatalogLayoutWatchdog(page);
    const hostileEvents = (testInfo as typeof testInfo & {
      hostileLoadEvents?: HostileLoadEvent[];
    }).hostileLoadEvents ?? [];

    await testInfo.attach('catalog-layout-watchdog.json', {
      body: JSON.stringify(snapshot, null, 2),
      contentType: 'application/json',
    });
    if (hostileEvents.length > 0) {
      await testInfo.attach('hostile-load.json', {
        body: JSON.stringify(hostileEvents, null, 2),
        contentType: 'application/json',
      });
    }

    expect(snapshot.sampledFrames, 'watchdog should sample continuously during load').toBeGreaterThan(0);
    expect(snapshot.latest, 'catalog auto-fill grid should reach a laid-out sample').not.toBeNull();
    expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
    expect(snapshot.latest?.resolvedTracks).toBe(snapshot.latest?.expected);
    expect(snapshot.latest?.acceptedColumns).toBe(snapshot.latest?.expected);

    const profile = hostileLoadProfile(testInfo);
    if (profile) {
      expect(hostileEvents.some(({ resourceType }) => resourceType === 'stylesheet'),
        `${profile} must delay at least one stylesheet response`).toBeTruthy();
      expect(hostileEvents.some(({ resourceType }) => resourceType === 'script'),
        `${profile} must delay at least one script response`).toBeTruthy();
      const stylesheetDelay = hostileEvents.find(
        ({ resourceType }) => resourceType === 'stylesheet',
      )?.delayMs ?? 0;
      const scriptDelay = hostileEvents.find(
        ({ resourceType }) => resourceType === 'script',
      )?.delayMs ?? 0;
      if (profile === 'css-slow') {
        expect(stylesheetDelay, 'css-slow must delay CSS longer than JavaScript')
          .toBeGreaterThan(scriptDelay);
      } else {
        expect(scriptDelay, 'script-slow must delay JavaScript longer than CSS')
          .toBeGreaterThan(stylesheetDelay);
      }
    }
  });
}
