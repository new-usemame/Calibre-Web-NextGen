import { test, expect } from '@playwright/test';

/*
 * #659 / #720 — "Couldn't find Admin/Settings view" in the new UI.
 *
 * The Admin entry point existed only as a Shield item in the sidebar rail.
 * Several admins looked in the account/avatar dropdown — the conventional place
 * for "Settings/Admin" — found only My account / Back to classic / Sign out, and
 * switched back to the classic UI because they couldn't find admin.
 *
 * The contract this pins: for an ADMIN user, the account (avatar) menu contains a
 * role-gated "Admin" link that navigates to the SPA's own /admin route. Without
 * the fix the account menu has no /admin link and this spec fails (red/green).
 *
 * The e2e default user (admin / admin123, cwn-local seed) is an admin, so the
 * item is expected present. Scoped to the account menu wrapper so it does not
 * accidentally pass on the separate sidebar Admin link.
 */

async function openAccountMenu(page: import('@playwright/test').Page) {
  await page.goto('/app');
  // Authed shell rendered.
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible({ timeout: 20_000 });
  const trigger = page.getByRole('button', { name: /account:/i });
  await expect(trigger).toBeVisible();
  await trigger.click();
  // Scope to the account menu wrapper (the div that owns the trigger) so we never
  // match the sidebar's own Admin link.
  return trigger.locator('xpath=ancestor::div[1]');
}

test.describe('account menu — Admin entry (#659/#720)', () => {
  test('desktop: admin sees an Admin link to /admin in the account menu', async ({ page }) => {
    const menu = await openAccountMenu(page);

    const adminLink = menu.getByRole('link', { name: 'Admin', exact: true });
    await expect(adminLink, 'account menu exposes an Admin item for admins').toBeVisible();
    await expect(adminLink).toHaveAttribute('href', /\/admin$/);

    // The link is wired to the SPA admin route (not a dead/placeholder link):
    // clicking it lands on the in-app admin page.
    await adminLink.click();
    await expect(page).toHaveURL(/\/admin(\/|$|\?)/);
  });

  test('mobile: admin sees the Admin link in the account menu too', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'mobile viewport project only');
    const menu = await openAccountMenu(page);

    const adminLink = menu.getByRole('link', { name: 'Admin', exact: true });
    await expect(adminLink).toBeVisible();
    await expect(adminLink).toHaveAttribute('href', /\/admin$/);
  });
});
