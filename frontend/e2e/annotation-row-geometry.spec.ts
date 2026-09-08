import { test, expect, type Page, type Locator } from '@playwright/test';

async function fixture(page: Page, count = 595) {
  await page.route('**/annotations/2/data.json', route => route.fulfill({ json: {
    annotation_count: count, devices: {},
    annotations: Array.from({ length: count }, (_, index) => ({
      annotation_id: `geometry-${index}`,
      highlighted_text: index % 3 === 2 ? '' : `Passage ${index}: ${'A long passage that wraps across lines even on a desktop. '.repeat(12)}`,
      position_type: index % 3 === 2 ? 'unanchored' : null,
      note_text: index % 3 === 1 ? null : `Note ${index} about this passage`,
      highlight_color: 'yellow', chapter_progress: null, source: 'kobo',
      origin_device_id: null, assigned_device_id: null, anchor_status: 'ok',
    })),
  } }));
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: { devices: [
    { public_id: 'reader', label: 'Reader', model: 'Kobo', type: 'kobo', active: true },
  ] } }));
  await page.route('**/annotations/2/geometry-*', route => route.fulfill({ json: {} }));
}

async function reachable(select: Locator) {
  await select.scrollIntoViewIfNeeded();
  await expect.poll(() => select.evaluate(element => {
    const own = element.closest('[data-virtual-row]')!.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return {
      contained: rect.top >= own.top && rect.bottom <= own.bottom && rect.left >= own.left && rect.right <= own.right,
      hit: hit === element || element.contains(hit),
    };
  }), { message: 'device selector must fit its own row and receive the hit at its centre' }).toEqual({ contained: true, hit: true });
}

test('wrapped highlights with notes keep their device selector inside the row and hit-testable', async ({ page }, testInfo) => {
  await fixture(page);
  await page.goto('/app/book/2/annotations');
  const rows = page.locator('[data-virtual-row]');
  await expect(rows.first()).toBeVisible();
  // The next row must be present: otherwise there is nothing to steal the hit.
  await expect(rows.nth(1)).toBeVisible();
  await reachable(rows.first().locator('select'));
  await rows.first().locator('select').selectOption('reader');
  await expect(rows.first().locator('select')).toHaveValue('reader');
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await reachable(rows.nth(1).locator('select')); // no note
  await reachable(rows.nth(2).locator('select')); // standalone note
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await reachable(rows.first().locator('select'));
  await page.getByLabel('Group by').selectOption('device');
  await expect.poll(() => rows.evaluateAll(elements => elements.every((element, index) => {
    const rect = element.getBoundingClientRect();
    const content = element.firstElementChild!.getBoundingClientRect();
    const next = elements[index + 1]?.getBoundingClientRect();
    return content.top >= rect.top && content.bottom <= rect.bottom && (!next || rect.bottom <= next.top + 0.1);
  }))).toBe(true);
  await page.getByLabel('Group by').selectOption('book');
  await page.setViewportSize({ width: testInfo.project.name === 'desktop' ? 390 : 800, height: 844 });
  await reachable(rows.first().locator('select'));
  // Layout measurement on mount and the resize fallback must also work on
  // browsers/test environments without ResizeObserver.
  await page.addInitScript(() => { Object.defineProperty(window, 'ResizeObserver', { value: undefined, configurable: true }); });
  await page.reload();
  await reachable(rows.first().locator('select'));
  await page.setViewportSize({ width: 375, height: 667 });
  await reachable(rows.first().locator('select'));
});

test('remeasurement anchors the visible passage and keeps a large list windowed', async ({ page }, testInfo) => {
  await fixture(page, 5950);
  await page.goto('/app/book/2/annotations');
  const viewport = page.locator('[data-virtualized-list]');
  await expect(page.locator('[data-virtual-row]').first()).toBeVisible();
  await viewport.evaluate(element => { element.scrollTop = 20000; });
  await expect.poll(() => page.locator('[data-virtual-row]').first().getAttribute('aria-posinset')).not.toBe('1');
  // Let the newly mounted estimates be replaced before changing a row above
  // the visible passage, like a late font/content update would do.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const before = await viewport.evaluate(element => {
    const rows = Array.from(element.querySelectorAll<HTMLElement>('[data-virtual-row]'));
    const top = element.getBoundingClientRect().top;
    const anchor = rows.find(row => row.getBoundingClientRect().bottom > top)!;
    const growing = rows[0];
    const result = { anchor: anchor.getAttribute('aria-posinset')!, y: anchor.getBoundingClientRect().top,
      growing: growing.getAttribute('aria-posinset')!, height: growing.getBoundingClientRect().height, mounted: rows.length };
    (growing.firstElementChild as HTMLElement).style.minHeight = `${result.height + 80}px`;
    return result;
  });
  const growing = page.locator(`[data-virtual-row][aria-posinset="${before.growing}"]`);
  await expect.poll(() => growing.evaluate(element => element.getBoundingClientRect().height)).toBeGreaterThan(before.height + 70);
  const anchor = page.locator(`[data-virtual-row][aria-posinset="${before.anchor}"]`);
  await expect.poll(async () => Math.abs((await anchor.boundingBox())!.y - before.y)).toBeLessThan(1);
  const counts = [before.mounted];
  for (const fraction of [0.5, 1, 0]) {
    await viewport.evaluate((element, fraction) => { element.scrollTop = element.scrollHeight * fraction; }, fraction);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const count = await page.locator('[data-virtual-row]').count();
    counts.push(count);
    expect(count).toBeLessThan(100);
  }
  console.log(`${testInfo.project.name}: total=5950, mounted at middle/half/end/top=${counts.join('/')}`);

  // Visit a whole list so every estimate is replaced, then compare the canvas
  // to the sum of real border boxes. Revisit the beginning to catch cache drift.
  await fixture(page, 60);
  await page.reload();
  await expect(page.locator('[data-virtual-row]').first()).toBeVisible();
  const geometry = await viewport.evaluate(async element => {
    const measured = new Map<number, number>();
    const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    let contiguous = true;
    for (let step = 0; step < 100; step++) {
      await settle();
      const rows = Array.from(element.querySelectorAll('[data-virtual-row]'));
      rows.forEach((row, index) => {
        const rect = row.getBoundingClientRect();
        measured.set(Number(row.getAttribute('aria-posinset')), rect.height);
        if (index) contiguous &&= Math.abs(rows[index - 1].getBoundingClientRect().bottom - rect.top) < 0.1;
      });
      if (element.scrollTop + element.clientHeight >= element.scrollHeight - 1) break;
      element.scrollTop += element.clientHeight / 2;
    }
    const canvas = element.querySelector('[role="list"]')!.getBoundingClientRect().height;
    element.scrollTop = 0;
    await settle();
    return { seen: measured.size, sum: [...measured.values()].reduce((a, b) => a + b, 0), canvas,
      revisited: element.querySelector('[role="list"]')!.getBoundingClientRect().height, contiguous };
  });
  expect(geometry.seen).toBe(60);
  expect(geometry.contiguous).toBe(true);
  expect(Math.abs(geometry.sum - geometry.canvas)).toBeLessThan(0.1);
  expect(geometry.revisited).toBe(geometry.canvas);
  console.log(`${testInfo.project.name}: measured all 60 rows, summed height=${geometry.sum}, canvas=${geometry.canvas}`);
});
