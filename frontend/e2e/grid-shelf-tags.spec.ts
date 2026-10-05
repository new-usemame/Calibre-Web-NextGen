import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * #1254 (@lguerard; #2261 asked the same): the classic grid names each book's
 * shelves on its cover, and the new UI's grid did not, so people switched back
 * to the classic view to see where a book was filed.
 */
test('a shelved book names its shelf on the library grid, and View settings can hide it', async ({ page }, testInfo) => {
  const csrf = await page.request.get('/api/v1/auth/csrf').then((r) => r.json()) as { csrf_token: string };
  const headers = { 'X-CSRFToken': csrf.csrf_token };
  await page.request.post('/api/v1/account/preferences', {
    headers, data: { preferences: { shelf_badges_hidden: false, card_actions_hidden: false } },
  });
  await page.addInitScript(() => {
    localStorage.removeItem('cwng:shelf-badges-hidden-v1');
    localStorage.removeItem('cwng:card-actions-hidden-v1');
  });

  // Label a book that is actually on screen: the grid order is per-user
  // (sort, filters), so the API's default first book may never render.
  await page.goto('/app');
  const firstCard = page.getByTestId('catalog-grid').locator('a[aria-label^="Open details for "]').first();
  await expect(firstCard).toBeVisible();
  const href = await firstCard.getAttribute('href');
  const bookId = Number(/\/book\/(\d+)/.exec(href ?? '')?.[1]);
  expect(bookId).toBeGreaterThan(0);
  const cardLabel = await firstCard.getAttribute('aria-label');
  // A leading digit sorts first in the default name order, so the tag is one
  // of the two shown by name rather than folded into "+N".
  const name = `0 Etagère e2e ${Date.now()}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok()).toBeTruthy();
  const shelfId = (await created.json() as { id: number }).id;
  try {
    expect((await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, { headers })).ok())
      .toBeTruthy();

    await page.reload();
    const card = page.getByTestId('catalog-grid').getByRole('link', { name: cardLabel!, exact: true }).first();
    // The narrow-cover summary also carries the full shelf-name title for
    // assistive/hover discovery. Assert against the actual visible badge so
    // the selector remains unambiguous when both badge and summary are present.
    const tag = card.getByTestId('shelf-tags').locator(`[class*="shelfBadge"][title="${name}"]`);
    if (testInfo.project.use.hasTouch === true) {
      // Narrow touch cards use a count to avoid covering the artwork, and the
      // action panel provides the full sighted label where hovering is absent.
      const shelfTags = card.getByTestId('shelf-tags');
      const summary = shelfTags.locator('[class*="shelfSummary"]');
      await expect(summary).toBeVisible();
      expect(await summary.getAttribute('aria-label')).toContain(name);
      const trigger = card.locator('..').getByRole('button', { name: `Actions for ${cardLabel!.replace(/^Open details for /, '')}`, exact: true });
      await trigger.tap();
      const dialog = page.getByRole('dialog', { name: /^Actions for / });
      await expect(dialog.getByRole('region', { name: 'Shelves' }).getByText(name, { exact: true })).toBeVisible();
      const accessibility = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
      expect(accessibility.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
      await page.keyboard.press('Escape');
    } else {
      await expect(tag).toBeVisible();

      // The tag stays inside the cover it labels.
      const [tagBox, coverBox] = await Promise.all([
        tag.boundingBox(),
        card.getByTestId('shelf-tags').locator('..').boundingBox(),
      ]);
      expect(tagBox && coverBox).toBeTruthy();
      expect(tagBox!.x).toBeGreaterThanOrEqual(coverBox!.x - 0.5);
      expect(tagBox!.x + tagBox!.width).toBeLessThanOrEqual(coverBox!.x + coverBox!.width + 0.5);
    }

    await page.getByTestId('catalog-view-settings').click();
    const toggle = page.getByTestId('show-shelf-tags');
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(toggle).not.toBeChecked();
    await expect(card.getByTestId('shelf-tags')).toHaveCount(0);
    if (testInfo.project.use.hasTouch === true) {
      await page.getByTestId('catalog-view-settings').click();
      await card.locator('..').getByRole('button', {
        name: `Actions for ${cardLabel!.replace(/^Open details for /, '')}`,
        exact: true,
      }).tap();
      const dialog = page.getByRole('dialog', { name: /^Actions for / });
      await expect(dialog.getByRole('region', { name: 'Shelves' })).toHaveCount(0);
      await page.keyboard.press('Escape');
      await page.getByTestId('catalog-view-settings').click();
    }
    await toggle.click();
    await expect(toggle).toBeChecked();
    if (testInfo.project.use.hasTouch === true) {
      await expect(card.getByTestId('shelf-tags').locator('[class*="shelfSummary"]')).toBeVisible();
      await card.locator('..').getByRole('button', {
        name: `Actions for ${cardLabel!.replace(/^Open details for /, '')}`,
        exact: true,
      }).tap();
      const dialog = page.getByRole('dialog', { name: /^Actions for / });
      await expect(dialog.getByRole('region', { name: 'Shelves' }).getByText(name, { exact: true })).toBeVisible();
      await page.keyboard.press('Escape');

      // Without a rendered action panel, narrow covers retain their original
      // visible shelf name instead of collapsing it to a count.
      const settingsMenu = page.getByTestId('catalog-view-settings-menu');
      if (!(await settingsMenu.isVisible())) await page.getByTestId('catalog-view-settings').click();
      const actionsToggle = page.getByTestId('show-card-actions');
      await expect(actionsToggle).toBeChecked();
      const hideActionsSave = page.waitForResponse(response =>
        response.url().includes('/api/v1/account/preferences')
        && response.request().method() === 'POST'
        && response.request().postDataJSON()?.preferences?.card_actions_hidden === true,
      );
      await actionsToggle.click();
      expect((await hideActionsSave).ok()).toBeTruthy();
      await expect(actionsToggle).not.toBeChecked();
      await expect(tag).toBeVisible();
      const restoreActionsSave = page.waitForResponse(response =>
        response.url().includes('/api/v1/account/preferences')
        && response.request().method() === 'POST'
        && response.request().postDataJSON()?.preferences?.card_actions_hidden === false,
      );
      await actionsToggle.click();
      expect((await restoreActionsSave).ok()).toBeTruthy();
      await expect(actionsToggle).toBeChecked();
    } else {
      await expect(tag).toBeVisible();
    }
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => undefined);
    await page.request.post('/api/v1/account/preferences', {
      headers,
      data: { preferences: { shelf_badges_hidden: false, card_actions_hidden: false } },
    }).catch(() => undefined);
  }
});
