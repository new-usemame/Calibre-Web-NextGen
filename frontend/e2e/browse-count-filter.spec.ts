import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

test.use({ contextOptions: { reducedMotion: 'reduce' } });

type Entity = { id: number; name: string; count: number };

const fixtures: Record<'authors' | 'series', { title: string; items: Entity[]; threshold: number }> = {
  authors: {
    title: 'Authors',
    threshold: 2,
    items: [
      { id: 101, name: 'One Book Author', count: 1 },
      { id: 102, name: 'Two Book Author', count: 2 },
      { id: 103, name: 'Many Book Author', count: 5 },
    ],
  },
  series: {
    title: 'Series',
    threshold: 5,
    items: [
      { id: 201, name: 'One Book Series', count: 1 },
      { id: 204, name: 'Four Book Series', count: 4 },
      { id: 205, name: 'Five Book Series', count: 5 },
    ],
  },
};

async function visibleRows(page: import('@playwright/test').Page) {
  return page.locator('main ul[role="list"] > li > a').evaluateAll((links) =>
    links.map((link) => {
      const spans = Array.from(link.querySelectorAll(':scope > span'));
      return { name: spans[0]?.textContent?.trim() ?? '', count: Number(spans[1]?.textContent) };
    }),
  );
}

test('Authors and Series combine an inclusive minimum with name search and report validation/results', async ({ page }) => {
  for (const plural of ['authors', 'series'] as const) {
    const fixture = fixtures[plural];
    await page.route(`**/api/v1/${plural}`, (route) =>
      route.fulfill({ json: { items: fixture.items } }),
    );
    await page.goto(`/app/${plural}`);
    await expect(page.getByRole('heading', { name: fixture.title, level: 1 })).toBeVisible();

    const minimum = page.getByRole('textbox', { name: 'Minimum books' });
    const nameFilter = page.getByRole('searchbox', { name: `Filter ${plural}` });
    await expect(minimum, 'count filters stay available even on a three-row list').toBeVisible();
    await expect(nameFilter).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: 'Showing 3 of 3' })).toBeVisible();
    expect(await visibleRows(page)).toEqual(fixture.items.map(({ name, count }) => ({ name, count })));

    await minimum.focus();
    await page.keyboard.type(String(fixture.threshold));
    const expected = fixture.items.filter((item) => item.count >= fixture.threshold);
    await expect(page.getByRole('status').filter({ hasText: `Showing ${expected.length} of 3` })).toBeVisible();
    expect(await visibleRows(page)).toEqual(expected.map(({ name, count }) => ({ name, count })));
    for (const theme of ['dark', 'light'] as const) {
      await page.evaluate(async (value) => {
        document.documentElement.setAttribute('data-theme', value);
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      }, theme);
      await test.info().attach(`${plural}-${test.info().project.name}-${theme}`, {
        body: await page.screenshot({ fullPage: true, type: 'jpeg', quality: 75 }),
        contentType: 'image/jpeg',
      });
    }
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), 'dark');

    await nameFilter.fill(expected[0].name.slice(0, 3).toLowerCase());
    const matching = expected.filter((item) =>
      item.name.toLocaleLowerCase().includes(expected[0].name.slice(0, 3).toLocaleLowerCase()),
    );
    await expect(page.getByRole('status').filter({ hasText: `Showing ${matching.length} of 3` })).toBeVisible();
    expect(await visibleRows(page)).toEqual(matching.map(({ name, count }) => ({ name, count })));

    await minimum.fill('1.5');
    await expect(minimum).toHaveAttribute('aria-invalid', 'true');
    await expect(page.locator('#minimum-books-error')).toHaveText('Enter a whole number of at least 1.');
    await expect(page.getByRole('status').filter({ hasText: 'Showing 0 of 3' })).toBeVisible();
    expect(await visibleRows(page)).toEqual([]);

    await minimum.fill('0');
    await expect(page.locator('#minimum-books-error')).toHaveText('Enter a whole number of at least 1.');
    await minimum.fill('');
    await expect(minimum).not.toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByRole('status').filter({ hasText: `Showing ${matching.length} of 3` })).toBeVisible();
    expect(await visibleRows(page)).toEqual(matching.map(({ name, count }) => ({ name, count })));
  }
});

test('minimum-books control is scoped to Authors and Series', async ({ page }) => {
  await page.route('**/api/v1/tags', (route) =>
    route.fulfill({ json: { items: [{ id: 301, name: 'A tag', count: 3 }] } }),
  );
  await page.goto('/app/tags');
  await expect(page.getByRole('heading', { name: 'Tags', level: 1 })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Minimum books' })).toHaveCount(0);
});

test('minimum books stay scoped while navigating browse lists from the sidebar', async ({ page }) => {
  const navigateFromSidebar = async (label: 'Authors' | 'Series' | 'Tags') => {
    if (test.info().project.name === 'mobile') {
      await page.getByRole('button', { name: 'Open navigation' }).click();
    }
    await page.getByRole('link', { name: label, exact: true }).first().click();
  };
  const authors = await (await page.request.get('/api/v1/authors')).json() as { items: Entity[] };
  const series = await (await page.request.get('/api/v1/series')).json() as { items: Entity[] };
  const tags = await (await page.request.get('/api/v1/tags')).json() as { items: Entity[] };
  await page.goto('/app/authors');
  await expect(page.getByRole('heading', { name: 'Authors', level: 1 })).toBeVisible();
  const origin = await page.evaluate(() => performance.timeOrigin);
  const minimum = page.getByRole('textbox', { name: 'Minimum books' });
  await minimum.fill('999');
  await expect(page.getByRole('status').filter({ hasText: `Showing 0 of ${authors.items.length}` })).toBeVisible();

  await navigateFromSidebar('Series');
  await expect(page.getByRole('heading', { name: 'Series', level: 1 })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Minimum books' })).toHaveValue('');
  await expect(page.getByRole('status').filter({ hasText: `Showing ${series.items.length} of ${series.items.length}` })).toBeVisible();
  expect(await visibleRows(page)).toHaveLength(series.items.length);

  await navigateFromSidebar('Tags');
  await expect(page.getByRole('heading', { name: 'Tags', level: 1 })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Minimum books' })).toHaveCount(0);
  await expect(page.locator('main').last().getByRole('link')).toHaveCount(tags.items.length);
  expect(await page.locator('main [role="status"]').count()).toBe(0);
  await navigateFromSidebar('Authors');
  await expect(page.getByRole('textbox', { name: 'Minimum books' })).toHaveValue('');
  await expect(page.getByRole('status').filter({ hasText: `Showing ${authors.items.length} of ${authors.items.length}` })).toBeVisible();
  expect(await visibleRows(page)).toHaveLength(authors.items.length);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(origin);
});

test('real Authors and Series payload counts drive the visible minimum filter', async ({ page }) => {
  for (const plural of ['authors', 'series'] as const) {
    const response = await page.request.get(`/api/v1/${plural}`);
    expect(response.ok(), `load the real ${plural} list payload`).toBeTruthy();
    const payload = await response.json() as { items: Entity[] };
    expect(payload.items.length, `the seeded rig has ${plural}`).toBeGreaterThan(0);
    expect(payload.items.every((item) => Number.isInteger(item.count) && item.count > 0)).toBe(true);

    const counts = [...new Set(payload.items.map((item) => item.count))].sort((a, b) => a - b);
    const threshold = counts.length > 1 ? counts[Math.floor(counts.length / 2)] : counts[0];
    await page.goto(`/app/${plural}`);
    await expect(page.getByRole('heading', { name: plural === 'authors' ? 'Authors' : 'Series', level: 1 })).toBeVisible();
    await page.getByRole('textbox', { name: 'Minimum books' }).fill(String(threshold));

    const expected = payload.items
      .filter((item) => item.count >= threshold)
      .map(({ name, count }) => ({ name, count }));
    await expect(page.getByRole('status').filter({ hasText: `Showing ${expected.length} of ${payload.items.length}` })).toBeVisible();
    const actual = await visibleRows(page);
    expect(actual).toHaveLength(expected.length);
    expect([...actual].sort((a, b) => a.name.localeCompare(b.name))).toEqual(
      [...expected].sort((a, b) => a.name.localeCompare(b.name)),
    );
  }
});

test('minimum filters remain accessible in both themes at the project viewport', async ({ page }) => {
  for (const plural of ['authors', 'series'] as const) {
    await page.goto(`/app/${plural}`);
    await expect(page.getByRole('heading', { name: plural === 'authors' ? 'Authors' : 'Series', level: 1 })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Minimum books' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await page.mouse.move(0, 0);
    for (const theme of ['light', 'dark'] as const) {
      await page.evaluate(async (value) => {
        document.documentElement.setAttribute('data-theme', value);
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      }, theme);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze();
      expect(results.violations.filter((violation) => ['critical', 'serious'].includes(violation.impact ?? '')),
        `${plural} list should have no critical or serious accessibility violations in ${theme} theme`)
        .toEqual([]);
    }
  }
});
