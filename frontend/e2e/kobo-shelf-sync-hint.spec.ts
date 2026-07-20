import { test, expect, Page } from '@playwright/test';

/*
 * #866 (@auspex) — marking a shelf "Kobo sync on" does nothing until the
 * account setting "Sync only selected shelves to Kobo" is also on. Nothing in
 * the new UI said so, so the reporter's device paged through all 11,000 books
 * and never settled on the six-book shelf.
 *
 * The shelf page now says it, on the shelf where the mark was made, with a
 * one-click fix. These tests pin the three states that matter:
 *   - marked + account setting OFF  -> notice shown
 *   - marked + account setting ON   -> no notice (nothing to fix)
 *   - not marked                    -> no notice (nothing was claimed)
 * and that the button actually flips the account setting.
 *
 * Skips itself when the instance has Kobo sync disabled server-side — the
 * "Enable Kobo sync" control does not exist then, and neither should the notice.
 *
 * "Sync only selected shelves" is an ACCOUNT-level flag shared by every browser
 * session of the same user, so these tests cannot run concurrently with each
 * other or with a second project — one would flip the flag out from under the
 * other. They run serially in one project and cover the mobile viewport by
 * resizing instead of by a second project.
 */

const NOTICE = /still set to sync your whole library/i;
const DESKTOP_ONLY = 'shared account flag — run once, resize for the mobile case';

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function setAccountShelfOnlySync(page: Page, on: boolean) {
  const res = await page.request.post('/api/v1/account/profile', {
    headers: { 'X-CSRFToken': await csrfToken(page) },
    data: { kobo_only_shelves_sync: on },
  });
  expect(res.ok(), 'account profile update should succeed').toBeTruthy();
}

test.describe('#866 Kobo shelf-sync hint', () => {
  test.describe.configure({ mode: 'serial' });

  test('warns on a Kobo-marked shelf while the account still syncs everything', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', DESKTOP_ONLY);
    const headers = { 'X-CSRFToken': await csrfToken(page) };

    const me = (await (await page.request.get('/api/v1/auth/me')).json()) as {
      features?: { kobo_sync?: boolean };
      kobo_only_shelves_sync?: boolean;
    };
    test.skip(!me.features?.kobo_sync, 'Kobo sync is disabled on this instance');
    const restore = me.kobo_only_shelves_sync === true;

    const created = await page.request.post('/api/v1/shelves', {
      headers,
      data: { name: `e2e-866-${testInfo.project.name}-${Date.now()}` },
    });
    expect(created.ok(), 'shelf create should succeed').toBeTruthy();
    const shelfId = ((await created.json()) as { id: number }).id;

    try {
      await setAccountShelfOnlySync(page, false);

      // Not marked for Kobo sync yet -> nothing to warn about.
      await page.goto(`/app/shelf/${shelfId}`);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.getByText(NOTICE)).toHaveCount(0);

      // Mark it the way the reporter did, from the shelf page itself.
      await page.getByRole('button', { name: /enable kobo sync/i }).click();
      await expect(page.getByText(NOTICE)).toBeVisible();

      // One click fixes it, and the notice goes away without a reload.
      await page.getByRole('button', { name: /sync only my selected shelves/i }).click();
      await expect(page.getByText(NOTICE)).toHaveCount(0);

      // The click really flipped the account setting, not just the local view.
      const after = (await (await page.request.get('/api/v1/account')).json()) as {
        kobo_only_shelves_sync: boolean;
      };
      expect(after.kobo_only_shelves_sync).toBe(true);

      // Still marked, setting now on -> stays quiet across a reload.
      await page.reload();
      await expect(page.getByText(NOTICE)).toHaveCount(0);
    } finally {
      await setAccountShelfOnlySync(page, restore).catch(() => {});
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
    }
  });

  test('the notice is readable on a phone', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', DESKTOP_ONLY);
    await page.setViewportSize({ width: 390, height: 844 });
    const headers = { 'X-CSRFToken': await csrfToken(page) };

    const me = (await (await page.request.get('/api/v1/auth/me')).json()) as {
      features?: { kobo_sync?: boolean };
      kobo_only_shelves_sync?: boolean;
    };
    test.skip(!me.features?.kobo_sync, 'Kobo sync is disabled on this instance');
    const restore = me.kobo_only_shelves_sync === true;

    const created = await page.request.post('/api/v1/shelves', {
      headers,
      data: { name: `e2e-866m-${testInfo.project.name}-${Date.now()}`, kobo_sync: true },
    });
    expect(created.ok(), 'shelf create should succeed').toBeTruthy();
    const shelfId = ((await created.json()) as { id: number }).id;

    try {
      await setAccountShelfOnlySync(page, false);
      await page.goto(`/app/shelf/${shelfId}`);

      const notice = page.getByText(NOTICE);
      await expect(notice).toBeVisible();
      // No horizontal overflow at 390px, and the fix stays tappable.
      const box = await notice.boundingBox();
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
      const btn = page.getByRole('button', { name: /sync only my selected shelves/i });
      const btnBox = await btn.boundingBox();
      expect(btnBox!.height).toBeGreaterThanOrEqual(36);
    } finally {
      await setAccountShelfOnlySync(page, restore).catch(() => {});
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
    }
  });
});
