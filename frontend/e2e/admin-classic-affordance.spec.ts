import { test, expect } from '@playwright/test';

test('advanced server links disclose the intentional classic-view transition (#909)', async ({ page }) => {
  await page.goto('/app/admin');
  const adminNav = page.getByRole('navigation', { name: 'Admin navigation' });
  await expect(adminNav).toBeVisible();
  const cards = adminNav.locator('a[href*="/admin/"], a[href$="/cwa-settings"], a[href$="/cwa-stats-show"]')
    .filter({ hasText: 'Opens in classic view' });
  await expect(cards).toHaveCount(8);

  // #1048 replaced this row. It used to read "Duplicate books" and link to
  // /app/duplicates — the exact page the sidebar already opens, which is why
  // @auspex reported the admin entry as doing nothing. From the admin panel the
  // useful destination is the duplicate-detection *configuration*, so it now
  // deep-links into the classic settings page and discloses that, like every
  // other classic destination here.
  //
  // This assertion was left pointing at the old row when that shipped, so the
  // spec has been red ever since — invisible because E2E does not gate PRs
  // (#953). Updated to the row that actually exists.
  const duplicates = adminNav.getByRole('link', { name: /Duplicates/ });
  await expect(duplicates).toHaveAttribute('href', /cwa-settings#duplicate-detection$/);
  await expect(duplicates).toContainText('Opens in classic view');
});
