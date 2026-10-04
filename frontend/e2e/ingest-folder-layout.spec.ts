import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow, collectPageErrors, assertNoPageErrors } from './utils';

// Exercise the real French catalog and settings component without changing an
// account locale or instance-wide ingest settings in the parallel CI lane.
for (const theme of ['light', 'dark']) {
  test(`translated folder settings action fits a phone (${theme})`, async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const writes: unknown[] = [];
    let settings = { target: 'tags', nested: false, custom_columns: [] };
    await page.route('**/api/v1/auth/me', async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), locale: 'fr' } });
    });
    await page.route('**/api/v1/admin/ingest-folder-label-settings', async route => {
      if (route.request().method() === 'PUT') {
        const values = route.request().postDataJSON();
        writes.push(values);
        settings = { ...settings, ...values };
      }
      await route.fulfill({ json: settings });
    });
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/app/admin#ingest-folder-labels');
    await page.evaluate(value => document.documentElement.setAttribute('data-theme', value), theme);
    const section = page.locator('#ingest-folder-labels');
    const save = section.getByRole('button', { name: 'Enregistrer les paramètres des dossiers d’import', exact: true });
    await expect(save).toBeVisible();
    await section.scrollIntoViewIfNeeded();
    await assertNoHorizontalOverflow(page);
    // A constrained border box must also contain its full label, rather than
    // clipping the wrapped text inside the shared Button's fixed height.
    expect(await save.evaluate(button => {
      const box = button.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(button);
      return Array.from(range.getClientRects()).every(text =>
        text.left >= box.left && text.right <= box.right + 1 &&
        text.top >= box.top && text.bottom <= box.bottom + 1);
    })).toBe(true);
    await save.focus();
    await expect(save).toBeFocused();
    await page.keyboard.press('Enter');
    await expect.poll(() => writes).toEqual([{ target: 'tags', nested: false }]);
    await expect(save).toBeEnabled();
    const axe = await new AxeBuilder({ page }).include('#ingest-folder-labels').analyze();
    expect(axe.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`folder-settings-fr-${theme}.jpg`), type: 'jpeg', quality: 75 });
    assertNoPageErrors(errors);
  });
}
