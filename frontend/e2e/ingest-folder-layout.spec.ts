import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow, collectPageErrors, assertNoPageErrors } from './utils';

// Exercise the real French catalog and settings component without changing an
// account locale or instance-wide ingest settings in the parallel CI lane.
for (const theme of ['light', 'dark']) {
  test(`translated administrator controls fit their columns (${theme})`, async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const writes: unknown[] = [];
    let settings = { target: 'tags', nested: false, custom_columns: [] };
    await page.route('**/api/v1/auth/me', async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), locale: 'fr', theme } });
    });
    await page.route('**/api/v1/admin/ingest-folder-label-settings', async route => {
      if (route.request().method() === 'PUT') {
        const values = route.request().postDataJSON();
        writes.push(values);
        settings = { ...settings, ...values };
      }
      await route.fulfill({ json: settings });
    });
    const width = testInfo.project.use.isMobile ? 375 : 1280;
    await page.setViewportSize({ width, height: width === 375 ? 667 : 800 });
    await page.goto('/app/admin#ingest-folder-labels');
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const section = page.locator('#ingest-folder-labels');
    const save = section.getByRole('button', { name: 'Enregistrer les paramètres des dossiers d’import', exact: true });
    await expect(save).toBeVisible();
    const font = page.locator('#library-settings select[aria-describedby="default-ui-font-help"]').first();
    await expect(font).toBeVisible();
    // Native WebKit selects can retain their longest option's intrinsic width
    // even though their grid column is narrower. Keep both font controls inside
    // their labelled field without losing their options or selected values.
    const controls = page.locator('#library-settings select[aria-describedby="default-ui-font-help"]');
    await expect(controls).toHaveCount(2);
    for (const control of await controls.all()) {
      const selected = await control.inputValue();
      expect(await control.evaluate(element => {
        const box = element.getBoundingClientRect();
        const field = element.parentElement!.getBoundingClientRect();
        return box.left >= field.left && box.right <= field.right + 1;
      })).toBe(true);
      await control.focus();
      await expect(control).toBeFocused();
      await control.selectOption('system-sans');
      await expect(control).toHaveValue('system-sans');
      await control.selectOption(selected);
    }
    await font.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`library-fonts-fr-${theme}-${width}.jpg`), type: 'jpeg', quality: 75 });
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
    const axe = await new AxeBuilder({ page }).include('#library-settings').include('#ingest-folder-labels').analyze();
    expect(axe.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`folder-settings-fr-${theme}-${width}.jpg`), type: 'jpeg', quality: 75 });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    assertNoPageErrors(errors);
  });
}
