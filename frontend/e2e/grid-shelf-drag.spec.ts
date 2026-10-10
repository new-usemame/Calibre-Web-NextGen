import { test as base, expect } from './fixtures';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { DEFAULT_SIDEBAR_ORDER, ORDERABLE_ENTRIES } from '../src/lib/sidebarEntries';

const test = base.extend<{ dragPage: Page }>({
  dragPage: async ({ browser, secondaryUser, baseURL }, use, info) => {
    const p = info.project.use;
    const context = await browser.newContext({ baseURL, viewport: p.viewport, isMobile: p.isMobile,
      hasTouch: p.hasTouch, userAgent: p.userAgent, deviceScaleFactor: p.deviceScaleFactor,
      storageState: await secondaryUser.context.storageState() });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    try { await use(page); expect(errors).toEqual([]); } finally { await context.close(); }
  },
});

// Wait for actual entrance/drawer animations before reading hit coordinates.
// A screenshot can span frames while a sidebar target is still moving.
async function settleAnimations(locator: import('@playwright/test').Locator) {
  await locator.evaluate(async element => {
    const animations: Animation[] = [];
    for (let ancestor: Element | null = element; ancestor; ancestor = ancestor.parentElement) {
      animations.push(...ancestor.getAnimations().filter(animation =>
        animation.playState === 'running' && Number.isFinite(animation.effect?.getComputedTiming().endTime)));
    }
    await Promise.all(animations.map(animation => animation.finished));
  });
}

test('a card offers an accessible shelf picker and persists membership', async ({ dragPage: page, secondaryUser }, info) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const books = await page.request.get('/api/v1/books?per_page=1');
  const book = (await books.json()).items[0] as { id: number; title: string };
  expect(book).toBeTruthy();
  const name = `Drag ${secondaryUser.username}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  try {
    await page.goto(`/app?q=${encodeURIComponent(book.title)}`);
    const handle = page.getByRole('button', { name: `Add ${book.title} to a shelf`, exact: true }).first();
    await expect(handle).toBeVisible();
    // A card's entrance animation translates its ancestor. Wait for its
    // natural finish before measuring the rendered 44px target.
    await handle.evaluate(async element => {
      const animations: Animation[] = [];
      for (let ancestor: Element | null = element; ancestor; ancestor = ancestor.parentElement) {
        animations.push(...ancestor.getAnimations().filter(animation => animation.playState === 'running'));
      }
      await Promise.all(animations.map(animation => animation.finished));
    });
    expect((await handle.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await handle.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Add to shelf', exact: true });
    await expect(dialog).toBeVisible();
    await info.attach('shelf-picker', { body: await page.screenshot({ path: info.outputPath('shelf-picker.jpeg'), type: 'jpeg', quality: 72 }), contentType: 'image/jpeg' });
    const axe = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    expect(axe.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
    await dialog.getByRole('button', { name, exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect.poll(async () => {
      const r = await page.request.get(`/api/v1/books/${book.id}/shelves`);
      return (await r.json()).shelf_ids as number[];
    }).toContain(shelf.id);
  } finally {
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});

test('twenty selected books drag together onto a sidebar shelf', async ({ dragPage: page, secondaryUser }, info) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const response = await page.request.get('/api/v1/books?per_page=20&sort=new');
  const books = (await response.json()).items as { id: number; title: string }[];
  expect(books).toHaveLength(20);
  const name = `Drag twenty ${secondaryUser.username}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  try {
    // Mirror a fresh install's full default navigation on this owned account.
    // A local instance can seed fewer entries and miss the lower shelf target
    // where the floating selection toolbar crosses the mobile drawer.
    const sidebar = await page.request.post('/api/v1/account/sidebar', { headers, data: {
      order: DEFAULT_SIDEBAR_ORDER,
      visibility: Object.fromEntries(ORDERABLE_ENTRIES.filter(e => !e.isShelvesBlock).map(e => [e.key, true])),
    } });
    expect(sidebar.ok(), `save owned sidebar preferences (${sidebar.status()})`).toBeTruthy();
    await page.addInitScript(() => localStorage.setItem('cwng:sidebar-pinned', 'false'));
    await page.goto('/app');
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    for (const book of books) {
      const card = page.getByRole('button', { name: `Select ${book.title}`, exact: true }).first();
      // Focus scrolls the real card into view without waiting on WebKit's
      // offscreen content-visibility geometry before it has been painted.
      await card.focus();
      await settleAnimations(card);
      await page.keyboard.press('Space');
    }
    await expect(page.getByRole('region', { name: '20 selected', exact: true })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    const selected = page.getByRole('button', { name: `Deselect ${books[0].title}`, exact: true }).first();
    await selected.scrollIntoViewIfNeeded();
    const target = page.locator(`[data-shelf-drop="${shelf.id}"]`);
    if (info.project.use.hasTouch) {
      // Chromium's input protocol produces trusted touchscreen events and
      // touch PointerEvents; dispatchEvent would not establish native dragging.
      const handle = page.getByRole('button', { name: `Add ${books[0].title} to a shelf`, exact: true }).first();
      await handle.scrollIntoViewIfNeeded();
      const box = (await handle.boundingBox())!;
      const x = box.x + box.width / 2, y = box.y + box.height / 2;
      const cdp = await page.context().newCDPSession(page);
      const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', px = x, py = y) => cdp.send('Input.dispatchTouchEvent', {
        type, touchPoints: type === 'touchEnd' ? [] : [{ x: px, y: py, id: 1 }],
      });
      await touch('touchStart'); await touch('touchMove', x - 14, y);
      await expect(target).toBeVisible();
      await target.scrollIntoViewIfNeeded();
      await settleAnimations(target);
      const dest = (await target.boundingBox())!;
      await touch('touchMove', dest.x + dest.width / 2, dest.y + dest.height / 2);
      await expect(target).toHaveClass(/dropOver/);
      await info.attach('shelf-drop-target', { body: await page.screenshot({ path: info.outputPath('shelf-drop-target.jpeg'), type: 'jpeg', quality: 72 }), contentType: 'image/jpeg' });
      // Keep the release over the target after the evidence capture; native
      // edge scrolling and drawer transitions can move it during a screenshot.
      const release = (await target.boundingBox())!;
      const nav = (await page.locator('[data-shelf-drag-nav]').boundingBox())!;
      // Follow the moving row out of the 48px edge-scroll band. The point
      // stays inside the lower shelf row and the floating toolbar's overlap.
      const releaseY = Math.min(release.y + release.height / 2, nav.y + nav.height - 56);
      expect(releaseY).toBeGreaterThan(release.y);
      expect(releaseY).toBeLessThan(release.y + release.height);
      await touch('touchMove', release.x + release.width / 2, releaseY);
      await expect(target).toHaveClass(/dropOver/);
      // This lower target can lie in the drawer's edge-scroll zone. Keep the
      // overlap hit above, then place the actual drop in the stable centre.
      await target.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest' }));
      const drop = (await target.boundingBox())!;
      await touch('touchMove', drop.x + drop.width / 2, drop.y + drop.height / 2);
      await expect(target).toHaveClass(/dropOver/);
      await touch('touchEnd'); await cdp.detach();
    } else {
      // The WebKit trace retained an off-viewport source box after scrolling.
      // Native hover resolves an actionable hit target before measuring input.
      await selected.hover();
      await settleAnimations(selected);
      const box = (await selected.boundingBox())!;
      const viewport = page.viewportSize()!;
      expect(box.x + box.width / 2).toBeGreaterThan(0);
      expect(box.x + box.width / 2).toBeLessThan(viewport.width);
      expect(box.y + box.height / 2).toBeGreaterThan(0);
      expect(box.y + box.height / 2).toBeLessThan(viewport.height);
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 16, box.y + box.height / 2, { steps: 4 });
      await expect(target).toBeVisible(); await target.scrollIntoViewIfNeeded();
      const dest = (await target.boundingBox())!;
      await page.mouse.move(dest.x + dest.width / 2, dest.y + dest.height / 2, { steps: 8 });
      await page.mouse.move(dest.x + dest.width / 2, dest.y + dest.height / 2);
      await expect(target).toHaveClass(/dropOver/);
      await info.attach('shelf-drop-target', { body: await page.screenshot({ path: info.outputPath('shelf-drop-target.jpeg'), type: 'jpeg', quality: 72 }), contentType: 'image/jpeg' });
      await page.mouse.up();
    }
    await expect.poll(async () => {
      const r = await page.request.get(`/api/v1/shelves/${shelf.id}?per_page=200`);
      return (await r.json()).items.map((b: { id: number }) => b.id).sort((a: number, b: number) => a - b);
    }).toEqual(books.map(b => b.id).sort((a, b) => a - b));
    await expect(page).toHaveURL(/\/app\/?$/);
    await expect(page.getByRole('region', { name: '20 selected', exact: true })).toBeVisible();
    // Re-dropping the same selection is idempotent; the picker is the keyboard
    // alternative to a second long drag.
    await page.evaluate(() => window.scrollTo(0, 0));
    const pickerHandle = page.getByRole('button', { name: `Add ${books[0].title} to a shelf`, exact: true }).first();
    await pickerHandle.focus(); await page.keyboard.press('Enter');
    await page.getByRole('dialog', { name: 'Add to shelf' }).getByRole('button', { name, exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Add to shelf' })).not.toBeVisible();
  } finally {
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});

test('a partial shelf failure retains failed books and retries only those books', async ({ dragPage: page, secondaryUser }) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const response = await page.request.get('/api/v1/books?per_page=2&sort=new');
  const books = (await response.json()).items as { id: number; title: string }[];
  const name = `Drag retry ${secondaryUser.username}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  const failedUrl = `**/api/v1/shelves/${shelf.id}/books/${books[1].id}`;
  try {
    await page.route(failedUrl, route => route.fulfill({ status: 503, json: {
      error: { code: 'unavailable', message: 'Temporary shelf failure' },
    } }));
    await page.goto('/app');
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    for (const book of books) {
      await page.getByRole('button', { name: `Select ${book.title}`, exact: true }).first().focus();
      await page.keyboard.press('Space');
    }
    const handle = page.getByRole('button', { name: `Add ${books[0].title} to a shelf`, exact: true }).first();
    await handle.focus(); await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Add to shelf', exact: true });
    await dialog.getByRole('button', { name, exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('Temporary shelf failure');
    await expect(dialog).toContainText('1 selected');
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).toBeVisible();
    const persisted = await page.request.get(`/api/v1/shelves/${shelf.id}?per_page=200`);
    expect((await persisted.json()).items.map((b: { id: number }) => b.id)).toEqual([books[0].id]);
    await page.unroute(failedUrl);
    const retryIds: number[] = [];
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().includes(`/shelves/${shelf.id}/books/`)) retryIds.push(Number(request.url().split('/').pop()));
    });
    await dialog.getByRole('button', { name, exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect(retryIds).toEqual([books[1].id]);
    const all = await page.request.get(`/api/v1/shelves/${shelf.id}?per_page=200`);
    expect((await all.json()).items.map((b: { id: number }) => b.id).sort((a: number, b: number) => a - b))
      .toEqual(books.map(b => b.id).sort((a, b) => a - b));
  } finally {
    await page.unroute(failedUrl);
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});

test('Escape cancels a handle pointer drag without writing and restores keyboard access', async ({ dragPage: page, secondaryUser }) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const response = await page.request.get('/api/v1/books?per_page=1');
  const book = (await response.json()).items[0] as { id: number; title: string };
  const name = `Drag cancel ${secondaryUser.username}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  let writes = 0;
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes(`/shelves/${shelf.id}/books/`)) writes++; });
  try {
    await page.addInitScript(() => localStorage.setItem('cwng:sidebar-pinned', 'false'));
    await page.goto(`/app?q=${encodeURIComponent(book.title)}`);
    const handle = page.getByRole('button', { name: `Add ${book.title} to a shelf`, exact: true }).first();
    await handle.scrollIntoViewIfNeeded();
    const box = (await handle.boundingBox())!;
    await page.mouse.move(box.x + 22, box.y + 22); await page.mouse.down();
    await page.mouse.move(box.x + 8, box.y + 22, { steps: 4 });
    await expect(page.locator(`[data-shelf-drop="${shelf.id}"]`)).toBeVisible();
    await page.keyboard.press('Escape'); await page.mouse.up();
    expect(writes).toBe(0);
    expect(await page.evaluate(() => localStorage.getItem('cwng:sidebar-pinned'))).toBe('false');
    await expect(page.getByRole('dialog', { name: 'Add to shelf' })).not.toBeVisible();
    await handle.focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog', { name: 'Add to shelf' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(handle).toBeFocused();
  } finally {
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});

test('a pending shelf failure does not restore its picker or selection after search navigation', async ({ dragPage: page, secondaryUser }) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const response = await page.request.get('/api/v1/books?per_page=2&sort=new');
  const books = (await response.json()).items as { id: number; title: string }[];
  const name = `Drag navigation ${secondaryUser.username}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const failedUrl = `**/api/v1/shelves/${shelf.id}/books/${books[0].id}`;
  try {
    await page.goto(`/app?q=${encodeURIComponent(books[1].title)}`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: `Add ${books[1].title} to a shelf`, exact: true }).first()).toBeVisible();
    const search = page.getByRole('searchbox', { name: 'Search the library' });
    if (!(await search.isVisible())) await page.getByRole('button', { name: 'Search the library', exact: true }).click();
    await search.fill(books[0].title); await search.press('Enter');
    await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe(books[0].title);
    await page.route(failedUrl, async route => { await gate; await route.fulfill({ status: 403, json: { error: 'Shelf write denied' } }); });
    const handle = page.getByRole('button', { name: `Add ${books[0].title} to a shelf`, exact: true }).first();
    await handle.focus(); await page.keyboard.press('Enter');
    const requested = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith(`/shelves/${shelf.id}/books/${books[0].id}`));
    await page.getByRole('dialog', { name: 'Add to shelf' }).getByRole('button', { name, exact: true }).click();
    await requested;
    await page.goBack();
    await expect(page.getByRole('dialog', { name: 'Add to shelf' })).not.toBeVisible();
    release();
    const currentHandle = page.getByRole('button', { name: `Add ${books[1].title} to a shelf`, exact: true }).first();
    await expect(currentHandle).toBeEnabled();
    await expect(page.getByRole('dialog', { name: 'Add to shelf' })).not.toBeVisible();
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).not.toBeVisible();
  } finally {
    release(); if (!page.isClosed()) await page.unroute(failedUrl);
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});

test('a pending catalog bulk action disables a second shelf action and selection changes', async ({ dragPage: page, secondaryUser }) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const response = await page.request.get('/api/v1/books?per_page=1');
  const book = (await response.json()).items[0] as { id: number; title: string };
  const name = `Drag busy ${secondaryUser.username}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const readUrl = `**/api/v1/books/${book.id}/read`;
  try {
    await page.route(readUrl, async route => { await gate; await route.fulfill({ json: { read: true } }); });
    await page.goto(`/app?q=${encodeURIComponent(book.title)}`);
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    const card = page.getByRole('button', { name: `Select ${book.title}`, exact: true }).first();
    await card.focus(); await page.keyboard.press('Space');
    await page.getByRole('button', { name: 'Mark read', exact: true }).click();
    const handle = page.getByRole('button', { name: `Add ${book.title} to a shelf`, exact: true }).first();
    await expect(handle).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: `Deselect ${book.title}`, exact: true }).first()).toBeDisabled();
    release();
    await expect(handle).toBeEnabled();
  } finally {
    release(); if (!page.isClosed()) await page.unroute(readUrl);
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});

test('View settings can hide the card grip while the card still drags (#2475)', async ({ dragPage: page, secondaryUser }) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name: `Grip ${secondaryUser.username}` } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelf = await created.json();
  const toggleGrip = async () => {
    await page.getByTestId('catalog-view-settings').click();
    const toggle = page.getByTestId('show-shelf-drag-handles');
    const saved = page.waitForResponse(r => r.url().includes('/api/v1/account/preferences') && r.request().method() === 'POST');
    await toggle.click();
    expect((await saved).ok()).toBeTruthy();
    await page.keyboard.press('Escape');
  };
  const grips = page.getByRole('button', { name: /^Add .+ to a shelf$/ });
  try {
    await page.goto('/app');
    await expect(grips.first(), 'the grip ships on').toBeVisible();
    await toggleGrip();
    await expect(grips).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('catalog-page')).toBeVisible();
    await expect(grips, 'the choice follows the account across a reload').toHaveCount(0);
    await expect(page.locator('a[draggable="true"][href*="/book/"]').first(), 'cards keep native drag').toBeVisible();
  } finally {
    if (!await grips.count()) await toggleGrip().catch(() => {});
    const removed = await secondaryUser.context.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
});
