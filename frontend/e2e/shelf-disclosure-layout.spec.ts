import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// A shelf picker may stay open while several shelves are toggled. Its panel
// must not cover part of a still-live download control beside/below it.
test('open shelf picker leaves book download targets unobscured', async ({ page }) => {
  await page.goto('/app');
  await page.getByTestId('catalog-grid').getByRole('link').first().click();
  const trigger = page.getByRole('button', { name: 'Add to shelf', exact: true });
  await trigger.click();
  await expect(page.getByRole('link', { name: 'Manage shelves', exact: true })).toBeVisible();
  await page.evaluate(async () => {
    const manage = [...document.querySelectorAll('a')].find(a => a.textContent === 'Manage shelves')!;
    await Promise.all(manage.parentElement!.getAnimations().map(a => a.finished));
  });
  const geometry = await page.evaluate(() => {
    const manage = [...document.querySelectorAll('a')].find(a => a.textContent === 'Manage shelves')!;
    const panel = manage.parentElement!;
    const p = panel.getBoundingClientRect();
    const downloads = [...document.querySelectorAll<HTMLAnchorElement>('a[download]')];
    return {
      count: downloads.length,
      covered: downloads.filter(a => {
        const r = a.getBoundingClientRect();
        return Math.min(p.right, r.right) > Math.max(p.left, r.left)
          && Math.min(p.bottom, r.bottom) > Math.max(p.top, r.top);
      }).map(a => a.textContent),
    };
  });
  expect(geometry.count, 'fixture must exercise at least one download control').toBeGreaterThan(0);
  expect(geometry.covered, 'shelf disclosure must not obscure still-live download targets').toEqual([]);
  const axe = await new AxeBuilder({ page }).withRules(['target-size']).analyze();
  expect(axe.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(page.getByRole('link', { name: 'Manage shelves', exact: true })).toHaveCount(0);
});
