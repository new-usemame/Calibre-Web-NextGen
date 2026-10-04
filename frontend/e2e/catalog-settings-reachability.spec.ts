import { test } from '@playwright/test';
import { expectSettingsReachable } from './catalog-settings-reachability';

for (const width of [1280, 360]) {
  test.describe(`catalog settings at ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });
    test('wrapped export toolbar keeps the panel and its last control reachable', async ({ page }) => {
      await expectSettingsReachable(page, width);
    });
  });
}
