import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';

async function expectBulkActionsReachable(page: Page) {
  // Check the visible phone viewport, not IntersectionObserver's potentially
  // expanded layout viewport. Every action must fit and receive a real click.
  const viewport = page.viewportSize()!;
  const bar = page.getByRole('region', { name: '2 selected', exact: true });
  await expect(bar).toBeVisible();
  for (const button of await bar.getByRole('button').all()) {
    const bounds = await button.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
    // A smart-shelf account may have no regular shelf to add books to.
    if (await button.isEnabled()) await button.click({ trial: true });
  }
}

async function headers(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return { 'X-CSRFToken': (await response.json()).csrf_token as string };
}

// Real API + isolated account: removing membership must change exactly the two
// chosen books, preserve the third, and refresh both kinds of shelf (#1939).
for (const kind of ['shelf', 'magic'] as const) {
  test(`${kind}: remove two selected books across pages from My Library`, async ({ page: admin, secondaryUser }) => {
    const page = secondaryUser.page;
    const adminHeaders = await headers(admin);
    const mode = await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
      headers: adminHeaders,
      data: { library_mode: 'personal_library', roles: { browse_global: true } },
    });
    expect(mode.ok(), await mode.text()).toBeTruthy();
    const userHeaders = await headers(page);
    const initial = await page.request.get('/api/v1/books?per_page=200');
    const initialIds = (await initial.json()).items.map((book: { id: number }) => book.id);
    if (initialIds.length) {
      const cleared = await page.request.post('/api/v1/books/my-library/batch', {
        headers: userHeaders, data: { operation: 'remove', book_ids: initialIds },
      });
      expect(cleared.ok(), await cleared.text()).toBeTruthy();
      expect((await cleared.json()).failed_ids).toEqual([]);
    }
    const global = await page.request.get('/api/v1/library/global?per_page=3&sort=abc');
    expect(global.ok(), await global.text()).toBeTruthy();
    const books = (await global.json()).items as Array<{ id: number; title: string }>;
    expect(books, 'seed the instance with at least three books').toHaveLength(3);
    for (const book of books) {
      const added = await page.request.put(`/api/v1/books/${book.id}/my-library`, { headers: userHeaders });
      expect(added.ok(), await added.text()).toBeTruthy();
    }
    // A long unbroken name must not widen the mobile layout viewport and
    // displace the fixed bulk controls. System-font metrics differ in CI.
    const name = `Bulk ${kind} ${secondaryUser.username} ${'W'.repeat(24)}`;
    const created = await page.request.post(kind === 'shelf' ? '/api/v1/shelves' : '/magicshelf', {
      headers: userHeaders,
      data: kind === 'shelf' ? { name } : {
        name, icon: '📚',
        rules: { condition: 'OR', rules: books.map(book => ({ id: 'title', operator: 'equal', value: book.title })) },
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
    const payload = await created.json();
    const shelfId = kind === 'shelf' ? payload.id : payload.shelf_id;
    expect(shelfId).toBeTruthy();
    try {
      if (kind === 'shelf') {
        for (const book of books) {
          const added = await page.request.post(`/api/v1/shelves/${shelfId}/books/${book.id}`, { headers: userHeaders });
          expect(added.ok(), await added.text()).toBeTruthy();
        }
      }
      // Keep the real server, but use small pages and hold page two until one
      // book is selected. This deterministically exercises append + selection.
      let releaseNextPage!: () => void;
      const nextPage = new Promise<void>(resolve => { releaseNextPage = resolve; });
      const endpoint = kind === 'shelf' ? `/api/v1/shelves/${shelfId}` : `/api/v1/magicshelf/${shelfId}`;
      await page.route(`**${endpoint}?*`, async route => {
        const url = new URL(route.request().url());
        url.searchParams.set('per_page', '2');
        if (url.searchParams.get('page') === '2') await nextPage;
        await route.continue({ url: url.toString() });
      });
      await page.addInitScript(({ kind, shelfId }) => {
        localStorage.setItem(kind === 'shelf' ? `cwng:shelf-sort-v1:${shelfId}` : `cwng:magic-shelf-sort:${shelfId}`, 'abc');
      }, { kind, shelfId });
      await page.goto(`/app/${kind}/${shelfId}`);
      await expect(page.getByRole('heading', { name: kind === 'magic' ? `📚 ${name}` : name, exact: true })).toBeVisible();
      const select = page.getByRole('button', { name: 'Select', exact: true });
      await expect(select).toHaveAttribute('aria-pressed', 'false');
      await select.click();
      await expect(page.getByRole('button', { name: 'Done', exact: true })).toHaveAttribute('aria-pressed', 'true');
      for (const book of [books[0], books[2]]) {
        // Keyboard activation also verifies the BookCard accessibility path.
        const card = page.getByRole('button', { name: `Select ${book.title}`, exact: true });
        await card.focus();
        await page.keyboard.press('Space');
        await expect(page.getByRole('button', { name: `Deselect ${book.title}`, exact: true })).toHaveAttribute('aria-pressed', 'true');
        releaseNextPage();
      }
      const bar = page.getByRole('region', { name: '2 selected', exact: true });
      await expect(bar).toBeVisible();
      await expectBulkActionsReachable(page);
      const accessibility = await new AxeBuilder({ page }).include('main').analyze();
      expect(accessibility.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`${kind}-bulk-selected.jpg`), type: 'jpeg', quality: 70 });
      await expect(bar.getByRole('button', { name: 'Delete from the global library' })).toHaveCount(0);
      let releaseMutation!: () => void;
      const pendingMutation = new Promise<void>(resolve => { releaseMutation = resolve; });
      await page.route('**/api/v1/books/my-library/batch', async route => {
        await pendingMutation;
        await route.continue();
      });
      page.once('dialog', dialog => dialog.accept());
      const mutation = page.waitForResponse(response => response.url().endsWith('/api/v1/books/my-library/batch') && response.request().method() === 'POST');
      await bar.getByRole('button', { name: 'Remove from my library', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeDisabled();
      await expect(bar.getByRole('button', { name: 'Clear selection' })).toBeDisabled();
      await expect(page.getByRole('button', { name: `Deselect ${books[0].title}`, exact: true })).toBeDisabled();
      releaseMutation();
      const response = await mutation;
      expect(response.ok(), await response.text()).toBeTruthy();
      expect(response.request().postDataJSON().book_ids).toEqual([books[0].id, books[2].id]);
      expect((await response.json()).failed_ids).toEqual([]);
      await expect(page.getByRole('region', { name: '2 selected', exact: true })).toHaveCount(0);
      await expect(page.locator('[aria-live]').filter({ hasText: '2 book(s) removed from your library.' })).toBeVisible();
      for (const book of [books[0], books[2]]) {
        await expect(page.locator('main').getByText(book.title, { exact: true })).toHaveCount(0);
      }
      await expect(page.getByRole('link', { name: `Open details for ${books[1].title}`, exact: true })).toBeVisible();
      const remaining = await page.request.get('/api/v1/books?per_page=200');
      expect((await remaining.json()).items.map((book: { id: number }) => book.id)).toEqual([books[1].id]);
      const globalAfter = await page.request.get('/api/v1/library/global?per_page=3&sort=abc');
      expect((await globalAfter.json()).items.map((book: { id: number }) => book.id)).toEqual(books.map(book => book.id));
    } finally {
      const deleted = await page.request.post(kind === 'shelf' ? `/api/v1/shelves/${shelfId}/delete` : `/magicshelf/${shelfId}/delete`, { headers: userHeaders });
      expect(deleted.ok(), await deleted.text()).toBeTruthy();
    }
  });
}

test('shelf: bulk read, reorder, route reuse and reversible per-card removal coexist', async ({ secondaryUser }) => {
  const page = secondaryUser.page;
  const userHeaders = await headers(page);
  const response = await page.request.get('/api/v1/books?per_page=3');
  expect(response.ok()).toBeTruthy();
  const books = (await response.json()).items as Array<{ id: number; title: string }>;
  expect(books, 'seed the instance with at least three books').toHaveLength(3);
  const shelves: Array<{ id: number; name: string }> = [];
  try {
    for (const suffix of ['A', 'B']) {
      const name = `Bulk modes ${secondaryUser.username} ${suffix} ${'W'.repeat(24)}`;
      const created = await page.request.post('/api/v1/shelves', { headers: userHeaders, data: { name } });
      expect(created.ok(), await created.text()).toBeTruthy();
      const { id } = await created.json();
      shelves.push({ id, name });
      for (const book of books) {
        const added = await page.request.post(`/api/v1/shelves/${id}/books/${book.id}`, { headers: userHeaders });
        expect(added.ok()).toBeTruthy();
      }
    }
    for (const book of books) {
      const unread = await page.request.post(`/api/v1/books/${book.id}/read`, { headers: userHeaders, data: { read: false } });
      expect(unread.ok()).toBeTruthy();
    }
    await page.goto(`/app/shelf/${shelves[0].id}`);
    await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Reorder', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Move down' })).toHaveCount(3);
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Move down' })).toHaveCount(0);
    for (const book of books.slice(0, 2)) {
      await page.getByRole('button', { name: `Select ${book.title}`, exact: true }).click();
    }
    const bar = page.getByRole('region', { name: '2 selected', exact: true });
    await expect(bar.getByRole('button', { name: 'Remove from my library' })).toHaveCount(0);
    await expectBulkActionsReachable(page);
    await bar.getByRole('button', { name: 'Mark read', exact: true }).click();
    await expect(page.locator('[aria-live="polite"]')).toHaveText('2 marked as read.');
    await expect(page.getByRole('img', { name: 'Read', exact: true })).toHaveCount(2);
    await expect(page.getByRole('button', { name: `Select ${books[2].title}`, exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Done', exact: true }).click();

    // Repeat an already-applied mutation: identical refetch results must not
    // leave the accumulator empty through structural sharing.
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    for (const book of books.slice(0, 2)) await page.getByRole('button', { name: `Select ${book.title}`, exact: true }).click();
    await bar.getByRole('button', { name: 'Mark read', exact: true }).click();
    await expect(page.getByRole('img', { name: 'Read', exact: true })).toHaveCount(2);
    await expect(page.getByRole('button', { name: `Select ${books[0].title}`, exact: true })).toBeVisible();
    await page.getByRole('button', { name: `Select ${books[0].title}`, exact: true }).click();
    await page.getByRole('button', { name: 'Reorder', exact: true }).click();
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Done reordering' }).click();
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    await page.getByRole('button', { name: `Select ${books[0].title}`, exact: true }).click();

    const openNavigation = page.getByRole('button', { name: 'Open navigation', exact: true });
    if (await openNavigation.isVisible()) await openNavigation.click();
    await page.getByRole('navigation').locator(`a[href="/app/shelf/${shelves[1].id}"]`).click();
    await expect(page.getByRole('heading', { name: shelves[1].name, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Select', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).toHaveCount(0);

    // Remove from shelf must stay an immediate membership action, with no
    // destructive confirmation and no effect on the user's library.
    let dialogs = 0;
    page.on('dialog', dialog => { dialogs++; void dialog.dismiss(); });
    const card = page.getByRole('link', { name: `Open details for ${books[0].title}`, exact: true });
    await card.hover();
    const more = page.getByRole('button', { name: `More actions for ${books[0].title}`, exact: true });
    if (await more.isVisible()) await more.click();
    const removal = page.waitForResponse(res => res.url().endsWith(`/shelves/${shelves[1].id}/books/${books[0].id}/delete`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Remove from shelf', exact: true }).filter({ visible: true }).first().click();
    expect((await removal).ok()).toBeTruthy();
    await expect(card).toHaveCount(0);
    expect(dialogs).toBe(0);
    const libraryBook = await page.request.get(`/api/v1/books/${books[0].id}`);
    expect(libraryBook.ok()).toBeTruthy();
  } finally {
    for (const shelf of shelves) {
      const removed = await page.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers: userHeaders });
      expect(removed.ok()).toBeTruthy();
    }
  }
});
