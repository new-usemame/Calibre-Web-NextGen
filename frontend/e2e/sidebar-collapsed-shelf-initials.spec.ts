import { test, expect, Page } from '@playwright/test';

/*
 * #2536 — on the collapsed desktop rail, shelf rows used to show the first
 * letters of their names. #2045 gave collapsed rows the expanded indent (to
 * keep the hover reveal free of layout work), which pushed every name past the
 * 64px rail: the rows became empty slots. The test reads what is actually
 * painted inside the rail, not which elements exist.
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

// Where the shelf's name is painted inside the rail: the left edge (relative to
// the nav) of each visible, opaque span carrying the name whose box overlaps
// the nav's own box.
async function paintedName(page: Page, shelfId: number, name: string): Promise<number[]> {
  return page.evaluate(([id, text]) => {
    const nav = document.querySelector('nav[aria-label="Browse"]');
    const link = document.querySelector(`nav[aria-label="Browse"] a[href$="/shelf/${id}"]`);
    if (!nav || !link) return [];
    const bounds = nav.getBoundingClientRect();
    return Array.from(link.querySelectorAll('span'))
      .filter((span) => {
        const style = getComputedStyle(span);
        const box = span.getBoundingClientRect();
        const overlap = Math.min(box.right, bounds.right) - Math.max(box.left, bounds.left);
        return style.display !== 'none' && style.visibility !== 'hidden'
          && Number(style.opacity) > 0.5 && overlap >= 8 && span.textContent === text;
      })
      .map((span) => Math.round(span.getBoundingClientRect().left - bounds.left));
  }, [shelfId, name] as const);
}

test('#2536 the collapsed desktop rail shows each shelf by its first letters', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop fine-pointer rail only');

  const headers = { 'X-CSRFToken': await csrfToken(page) };
  const name = `Zebra e2e-2536-${Date.now()}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), 'shelf create should succeed').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;

  try {
    await page.addInitScript(() => {
      localStorage.setItem('cwng:sidebar-pinned', '0');
      localStorage.setItem('cwng:shelves-expanded', '1');
    });
    await page.goto('/app/');
    const nav = page.getByRole('navigation', { name: 'Browse' });
    await page.mouse.move(1000, 300);
    await expect.poll(() => nav.evaluate((el) => getComputedStyle(el).width)).toBe('64px');
    await expect(nav.locator(`a[href$="/shelf/${shelfId}"]`)).toBeAttached();

    // Collapsed: the row paints the start of the name in the rail's icon column.
    await expect.poll(() => paintedName(page, shelfId, name)).toEqual([expect.any(Number)]);
    expect((await paintedName(page, shelfId, name))[0]).toBeLessThan(32);

    // Revealed: the name is painted once, at the indented text column (the
    // initials fade out), and the link's accessible name carries it once.
    await nav.hover({ position: { x: 32, y: 80 } });
    await expect.poll(() => nav.evaluate((el) => getComputedStyle(el).width)).toBe('220px');
    await expect.poll(() => paintedName(page, shelfId, name)).toEqual([expect.any(Number)]);
    expect((await paintedName(page, shelfId, name))[0]).toBeGreaterThanOrEqual(40);
    await expect(nav.getByRole('link', { name: new RegExp(`^${name}\\b`) })).toBeVisible();
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
  }
});
