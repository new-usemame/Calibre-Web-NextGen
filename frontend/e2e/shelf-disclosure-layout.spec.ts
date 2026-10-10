import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// A shelf picker may stay open while several shelves are toggled. It must not
// cover any still-live control on the book page: covered author links, tag
// links and tag editing buttons stay clickable and reachable underneath it.
test('open shelf picker leaves the book page controls unobscured', async ({ page }) => {
  const list = await page.request.get('/api/v1/books?sort=new&per_page=50');
  expect(list.ok()).toBeTruthy();
  let bookId: number | undefined;
  for (const item of (await list.json()).items as Array<{ id: number }>) {
    const detail = await (await page.request.get(`/api/v1/books/${item.id}`)).json();
    if (detail.tags?.length && detail.authors?.length) { bookId = item.id; break; }
  }
  expect(bookId, 'fixture needs a book with authors and tags below its actions').toBeTruthy();
  await page.goto(`/app/book/${bookId}`);
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
    const picker = panel.parentElement!;
    const p = panel.getBoundingClientRect();
    const live = [...document.querySelectorAll<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )].filter(element => !picker.contains(element) && element.getClientRects().length > 0);
    return {
      count: live.length,
      covered: live.filter(element => {
        const r = element.getBoundingClientRect();
        return Math.min(p.right, r.right) > Math.max(p.left, r.left)
          && Math.min(p.bottom, r.bottom) > Math.max(p.top, r.top);
      }).map(element => `${element.tagName.toLowerCase()} "${(element.getAttribute('aria-label')
        || element.textContent || '').trim().slice(0, 40)}"`),
    };
  });
  expect(geometry.count, 'the book page renders live controls outside the picker').toBeGreaterThan(0);
  expect(geometry.covered, 'shelf disclosure must not obscure still-live controls').toEqual([]);
  const axe = await new AxeBuilder({ page }).withRules(['target-size']).analyze();
  expect(axe.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(page.getByRole('link', { name: 'Manage shelves', exact: true })).toHaveCount(0);
});
