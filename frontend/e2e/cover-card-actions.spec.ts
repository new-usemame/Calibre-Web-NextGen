import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

const cards = 'a[aria-label^="Open details for"]';

test('cover actions persist favorite/read choices without opening the book', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  const phone = test.info().project.use.hasTouch === true;
  await page.setViewportSize(phone ? { width: 375, height: 812 } : { width: 1280, height: 800 });
  await page.goto('/app');
  const first = page.getByTestId('catalog-grid').locator(cards).first();
  await expect(first).toBeVisible();
  const href = (await first.getAttribute('href'))!;
  const details = page.getByTestId('catalog-grid').locator(`a[href="${href}"]`);
  await expect(details).toBeVisible();
  const id = (await details.getAttribute('href'))!.match(/\/book\/(\d+)/)![1];
  const card = details.locator('..');
  const trigger = card.getByRole('button', { name: /^Actions for / });
  const open = async () => {
    if (phone) await trigger.tap();
    else { await details.hover(); await trigger.click(); }
    await expect(page.getByRole('dialog', { name: /^Actions for / })).toBeVisible();
  };
  const initial = await (await page.request.get(`/api/v1/books/${id}`)).json();
  await open();
  const favorite = page.getByRole('button', { name: initial.favorited ? 'Remove from favorites' : 'Add to favorites', exact: true });
  const savedFavorite = page.waitForResponse(r => r.url().endsWith(`/api/v1/books/${id}/favorite`) && r.request().method() === 'POST');
  await favorite.click();
  expect((await savedFavorite).ok()).toBeTruthy();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect.poll(async () => (await (await page.request.get(`/api/v1/books/${id}`)).json()).favorited).toBe(!initial.favorited);
  await expect(page).toHaveURL(/\/app\/?$/);
  await page.reload();
  await expect(card.getByTestId('favorite-badge')).toHaveCount(initial.favorited ? 0 : 1);
  await open();
  const savedRead = page.waitForResponse(r => r.url().endsWith(`/api/v1/books/${id}/read`) && r.request().method() === 'POST');
  await page.getByRole('button', { name: initial.read ? 'Mark as unread' : 'Mark as read', exact: true }).click();
  expect((await savedRead).ok()).toBeTruthy();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();
  await expect(card.getByTestId('read-badge')).toHaveCount(initial.read ? 0 : 1);
  await open();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  // Audit the page at its origin: card focus can naturally scroll an unfocused
  // toolbar control partly behind the sticky header. Focus reachability has
  // its own natural-scroll regression below. Keep every Axe rule enabled.
  await page.evaluate(() => window.scrollTo(0, 0));
  const violations = (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()).violations.filter(v => ['critical', 'serious'].includes(v.impact || ''));
  expect(violations).toEqual([]);
});

for (const density of ['Comfortable', 'Compact', 'Dense']) {
  test(`cover action stays inside the cover and its panel is readable (${density})`, async ({ secondaryUser }, info) => {
    const { page } = secondaryUser;
    const phone = info.project.use.hasTouch === true;
    await page.setViewportSize({ width: phone ? 320 : 1280, height: phone ? 740 : 800 });
    await page.goto('/app');
    await page.getByTestId('catalog-view-settings').click();
    await page.getByRole('radio', { name: density, exact: true }).check();
    await expect(page.getByTestId('catalog-grid')).toHaveClass(new RegExp(`density_${density.toLowerCase()}`));
    await page.keyboard.press('Escape');
    const details = page.getByTestId('catalog-grid').locator(cards).first();
    await expect(details).toBeVisible();
    const card = details.locator('..');
    const trigger = card.getByRole('button', { name: /^Actions for / });
    await trigger.focus();
    const geometry = await trigger.evaluate(node => {
      const card = node.closest('[class*="wrap"]')!;
      const cover = card.querySelector('[class*="coverWrap"]')!.getBoundingClientRect();
      const button = node.getBoundingClientRect();
      const metadata = card.querySelector('[class*="info"]')!.getBoundingClientRect();
      return { cover: cover.toJSON(), button: button.toJSON(), metadata: metadata.toJSON() };
    });
    expect(geometry.button.width).toBeGreaterThanOrEqual(24);
    expect(geometry.button.height).toBeGreaterThanOrEqual(24);
    expect(geometry.button.left).toBeGreaterThanOrEqual(geometry.cover.left);
    expect(geometry.button.right).toBeLessThanOrEqual(geometry.cover.right);
    expect(geometry.button.top).toBeGreaterThanOrEqual(geometry.cover.top);
    expect(geometry.button.bottom).toBeLessThanOrEqual(geometry.cover.bottom);
    expect(geometry.button.bottom).toBeLessThan(geometry.metadata.top);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
      if (phone) await trigger.tap(); else await trigger.press('Enter');
      const panel = page.getByRole('dialog', { name: /^Actions for / });
      await expect(panel).toBeVisible();
      const box = (await panel.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height);
      const controls = panel.locator('button, a');
      await controls.last().focus();
      await page.keyboard.press('Tab');
      await expect(controls.first()).toBeFocused();
      await page.keyboard.press('Shift+Tab');
      await expect(controls.last()).toBeFocused();
      await page.screenshot({ path: info.outputPath(`cover-actions-${density}-${theme}.jpg`), type: 'jpeg', quality: 75 });
      const axe = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
      expect(axe.violations.filter(v => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
    }
  });
}

test('failed card mutation retains state and can be retried', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  await page.goto('/app');
  const details = page.getByTestId('catalog-grid').locator(cards).first();
  await expect(details).toBeVisible();
  const href = (await details.getAttribute('href'))!;
  const id = href.match(/\/book\/(\d+)/)![1];
  const card = details.locator('..');
  const trigger = card.getByRole('button', { name: /^Actions for / });
  const before = await (await page.request.get(`/api/v1/books/${id}`)).json();
  await page.route(`**/api/v1/books/${id}/favorite`, route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":{"message":"owned failure probe"}}' }));
  try {
    await trigger.focus(); await trigger.press('Enter');
    const button = page.getByRole('button', { name: before.favorited ? 'Remove from favorites' : 'Add to favorites', exact: true });
    await button.click();
    await expect(page.getByRole('dialog')).toContainText('Could not update this book. Try again.');
    await expect(button).toBeEnabled();
    expect((await (await page.request.get(`/api/v1/books/${id}`)).json()).favorited).toBe(before.favorited);
    await page.unroute(`**/api/v1/books/${id}/favorite`);
    await button.click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect((await (await page.request.get(`/api/v1/books/${id}`)).json()).favorited).toBe(!before.favorited);
  } finally {
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('hiding card actions removes disclosure and selection retains one toggle', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  await page.goto('/app');
  const grid = page.getByTestId('catalog-grid');
  await expect(grid.getByRole('button', { name: /^Actions for / }).first()).toBeAttached();
  await page.getByTestId('catalog-view-settings').click();
  const saved = page.waitForResponse(r => r.url().includes('/api/v1/account/preferences') && r.request().method() === 'POST');
  await page.getByTestId('show-card-actions').click();
  await expect(page.getByTestId('show-card-actions')).not.toBeChecked();
  expect((await saved).ok()).toBeTruthy();
  await page.keyboard.press('Escape');
  await expect(grid.getByRole('button', { name: /^Actions for / })).toHaveCount(0);
  await expect(grid.locator(cards).first()).toBeVisible();
  await page.reload();
  await expect(grid.getByRole('button', { name: /^Actions for / })).toHaveCount(0);
  await page.getByTestId('catalog-view-settings').click();
  const restored = page.waitForResponse(r => r.url().includes('/api/v1/account/preferences')
    && r.request().method() === 'POST'
    && r.request().postDataJSON()?.preferences?.card_actions_hidden === false);
  await page.getByTestId('show-card-actions').click();
  expect((await restored).ok()).toBeTruthy();
  await expect(page.getByTestId('show-card-actions')).toBeChecked();
  await page.keyboard.press('Escape');
  await expect(grid.getByRole('button', { name: /^Actions for / }).first()).toBeAttached();
  await page.getByTitle('Select multiple', { exact: true }).click();
  await expect(grid.getByRole('button', { name: /^Actions for / })).toHaveCount(0);
  const toggles = grid.getByRole('button', { name: /^Select / });
  await expect(toggles.first()).toBeVisible();
  const title = (await toggles.first().getAttribute('aria-label'))!.replace(/^Select /, '');
  await toggles.first().click();
  await expect(grid.getByRole('button', { name: `Deselect ${title}`, exact: true })).toHaveAttribute('aria-pressed', 'true');
});


for (const filter of ['favorites', 'unread', 'default-unread'] as const) {
  test(`card mutation rebuilds ${filter} membership and restores keyboard focus`, async ({ secondaryUser }) => {
    const { page } = secondaryUser;
    await page.goto('/app');
    const first = page.getByTestId('catalog-grid').locator(cards).first();
    await expect(first).toBeVisible();
    const href = (await first.getAttribute('href'))!;
    const id = href.match(/\/book\/(\d+)/)![1];
    const csrf = await (await page.request.get('/api/v1/auth/csrf')).json();
    const headers = { 'X-CSRFToken': csrf.csrf_token };
    if (filter === 'favorites') {
      const current = await (await page.request.get(`/api/v1/books/${id}`)).json();
      if (!current.favorited) {
        expect((await page.request.post(`/api/v1/books/${id}/favorite`, { headers })).ok()).toBeTruthy();
      }
      await page.goto('/app/favorites');
    } else {
      expect((await page.request.post(`/api/v1/books/${id}/read`, { headers, data: { read: false } })).ok()).toBeTruthy();
      if (filter === 'default-unread') {
        expect((await page.request.post('/ajax/view', { headers, data: { catalog: { default_filter: { read_status: 'unread' } } } })).ok()).toBeTruthy();
        await page.reload();
      } else {
        await page.getByRole('group', { name: 'Read status filter' }).getByRole('button', { name: 'Unread', exact: true }).click();
      }
    }
    const details = page.getByTestId('catalog-grid').locator(`a[href="${href}"]`);
    await expect(details).toBeVisible();
    const beforeCount = Number((await page.getByTestId('catalog-count').textContent())!.match(/\d+/)![0]);
    const trigger = details.locator('..').getByRole('button', { name: /^Actions for / });
    if (test.info().project.use.hasTouch === true) await trigger.tap();
    else { await details.hover(); await trigger.click(); }
    await page.getByRole('dialog').getByRole('button', {
      name: filter === 'favorites' ? 'Remove from favorites' : 'Mark as read', exact: true,
    }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.locator('[aria-live="polite"]').filter({ hasText: 'Saved.' })).toBeAttached();
    await expect(details).toHaveCount(0);
    if (beforeCount === 1) {
      await expect(page.getByTestId('catalog-count')).toHaveCount(0);
      await expect(page.getByText('No books here.', { exact: true })).toBeVisible();
    } else {
      await expect.poll(async () => Number((await page.getByTestId('catalog-count').textContent())!.match(/\d+/)![0])).toBe(beforeCount - 1);
    }
    await expect(page.getByTestId('catalog-heading')).toBeFocused();
    const persisted = await (await page.request.get(`/api/v1/books/${id}`)).json();
    expect(filter === 'favorites' ? persisted.favorited : !persisted.read).toBe(false);
    await expect(page.locator('[aria-live="polite"]').filter({ hasText: 'Saved.' })).toBeAttached();
  });
}


test('unavailable favorite state keeps read controls without guessing a favorite action', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  await page.route('**/api/v1/books?*', async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.items = body.items.map((book: Record<string, unknown>) => ({ ...book, favorited: null }));
    await route.fulfill({ response, json: body });
  });
  try {
    await page.goto('/app');
    const card = page.getByTestId('catalog-grid').locator(cards).first().locator('..');
    const trigger = card.getByRole('button', { name: /^Actions for / });
    if (test.info().project.use.hasTouch === true) await trigger.tap();
    else { await card.hover(); await trigger.click(); }
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('button', { name: /favorites/ })).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: /Mark as (unread|read)/ })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
  } finally { await page.unrouteAll({ behavior: 'wait' }); }
});


test('worst dense cover badge stack does not collide with the action disclosure', async ({ secondaryUser }, info) => {
  const { page } = secondaryUser;
  await page.setViewportSize({ width: 320, height: 740 });
  const booksResponse = await page.request.get('/api/v1/books?page=1&per_page=24');
  expect(booksResponse.ok()).toBeTruthy();
  const books = await booksResponse.json();
  const book = books.items[0];
  const fixture = {
    ...book,
    in_progress: true,
    read: false,
    hidden: true,
    favorited: true,
    in_my_library: true,
    shelves: [{ id: 701, name: 'Shelf with a long name' }, { id: 702, name: 'Second shelf' }, { id: 703, name: 'Third shelf' }],
  };

  await page.route('**/api/v1/auth/me', async route => {
    const response = await route.fetch();
    const me = await response.json();
    me.library_mode = 'personal_library';
    me.role = { ...me.role, browse_global: true, viewer: true, anonymous: false };
    await route.fulfill({ response, json: me });
  });
  await page.route('**/api/v1/library/global?*', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ items: [fixture], page: 1, per_page: 24, total: 1 }),
  }));
  await page.addInitScript(() => localStorage.setItem('cwng:catalog-density-v1', 'dense'));
  await page.goto('/app/global');
  await page.reload();

  const main = page.getByTestId('global-library-page');
  const detail = main.locator('a[aria-label^="Open details for"]').first();
  await expect(detail).toBeVisible();
  const card = detail.locator('..');
  const cover = card.locator('[class*="coverWrap"]');
  const badgeRow = cover.locator('[class*="badgeRow"]');
  const trigger = card.getByRole('button', { name: /^Actions for / });
  await expect(trigger).toBeVisible();
  await expect(badgeRow.getByTestId('reading-badge')).toBeVisible();
  await expect(badgeRow.getByTestId('hidden-book-badge')).toBeVisible();
  await expect(badgeRow.getByRole('img', { name: 'Favorite' })).toBeVisible();
  await expect(badgeRow.getByRole('img', { name: 'In your library' })).toBeVisible();

  const geometry = await page.evaluate(() => {
    const card = document.querySelector('[data-testid="global-library-page"] a[aria-label^="Open details for"]')?.parentElement;
    const cover = card?.querySelector('[class*="coverWrap"]');
    const trigger = card?.querySelector('button[aria-label^="Actions for"]');
    const badgeRow = cover?.querySelector('[class*="badgeRow"]');
    if (!cover || !trigger || !badgeRow) return null;
    const rect = (el: Element) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
    };
    return {
      cover: rect(cover),
      trigger: rect(trigger),
      row: rect(badgeRow),
      badges: Array.from(badgeRow.querySelectorAll('[role="img"]')).map(el => ({
        label: el.getAttribute('aria-label'), ...rect(el),
      })),
    };
  });
  expect(geometry).not.toBeNull();
  expect(geometry!.row.y).toBeGreaterThanOrEqual(geometry!.cover.y);
  expect(geometry!.row.bottom).toBeLessThanOrEqual(geometry!.cover.bottom);
  for (const badge of geometry!.badges) {
    expect(badge.x).toBeGreaterThanOrEqual(geometry!.cover.x);
    expect(badge.right).toBeLessThanOrEqual(geometry!.cover.right);
  }
  const shelfSummary = card.locator('[class*="shelfSummary"]');
  await expect(shelfSummary).toBeVisible();
  await expect(shelfSummary).toHaveAttribute('aria-label', 'On shelf Shelf with a long name, Second shelf, Third shelf');
  const shelfBox = (await shelfSummary.boundingBox())!;
  expect(shelfBox.x).toBeGreaterThanOrEqual(geometry!.cover.x);
  expect(shelfBox.x + shelfBox.width).toBeLessThanOrEqual(geometry!.cover.right);
  expect(shelfBox.y + shelfBox.height).toBeLessThanOrEqual(geometry!.row.y);
  expect(shelfBox.x).toBeGreaterThanOrEqual(geometry!.trigger.right);

  await page.screenshot({ path: info.outputPath('dense-all-badges.jpg'), type: 'jpeg', quality: 75 });
  console.log('dense cover geometry', JSON.stringify(geometry));
  const collisions = geometry!.badges.filter(badge =>
    Math.min(geometry!.trigger.right, badge.right) - Math.max(geometry!.trigger.x, badge.x) > 0.5
    && Math.min(geometry!.trigger.bottom, badge.bottom) - Math.max(geometry!.trigger.y, badge.y) > 0.5);
  expect(collisions, `cover action overlaps status badges: ${JSON.stringify(geometry)}`).toEqual([]);
  await page.unrouteAll({ behavior: 'wait' });
});


test('catalog focus keeps toolbar controls below the sticky header after card actions', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  const phone = test.info().project.use.hasTouch === true;
  await page.setViewportSize(phone ? { width: 375, height: 812 } : { width: 1280, height: 800 });
  await page.goto('/app');
  const card = page.getByTestId('catalog-grid').locator(cards).first().locator('..');
  const trigger = card.getByRole('button', { name: /^Actions for / });
  if (phone) await trigger.tap(); else { await trigger.focus(); await trigger.press('Enter'); }
  await expect(page.getByRole('dialog', { name: /^Actions for / })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  const sort = page.getByRole('combobox', { name: 'Sort order' });
  await sort.focus();
  await expect(sort).toBeFocused();
  const geometry = await sort.evaluate(control => ({
    control: control.getBoundingClientRect().toJSON(),
    header: document.querySelector('header')!.getBoundingClientRect().toJSON(),
  }));
  expect(geometry.control.top).toBeGreaterThanOrEqual(geometry.header.bottom);
  expect(geometry.control.bottom).toBeLessThanOrEqual(page.viewportSize()!.height);
});
