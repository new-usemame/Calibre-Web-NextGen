import { test, expect } from './fixtures';

// These contracts isolate failed HTTP responses and paging transitions. The
// separately seeded real-library checks cover actual values and permissions.
test('custom-column request failures remain errors and can be retried', async ({ page }) => {
  let available = false;
  await page.route('**/api/v1/columns', async route => {
    await route.fulfill({ status: available ? 200 : 503, json: available
      ? { items: [{ id: 77, name: 'Subjects', datatype: 'text', hierarchical: true }] }
      : { error: { code: 'unavailable', message: 'Column definitions temporarily unavailable' } } });
  });
  await page.goto('/app/cc');
  await expect(page.getByRole('heading', { name: 'Could not load columns', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No custom columns to browse' })).toHaveCount(0);
  available = true;
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Subjects Tree', exact: true })).toBeVisible();

  await page.route('**/api/v1/columns/77/tree', route => route.fulfill({ status: 404,
    json: { error: { code: 'not_found', message: 'Column not found' } } }));
  await page.getByRole('link', { name: 'Subjects Tree', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Could not load column', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No values in this column yet' })).toHaveCount(0);
});

test('custom-column paging resets for a new node without showing old-node books', async ({ page }) => {
  const bookPage = await page.request.get('/api/v1/books?per_page=1').then(r => r.json());
  expect(bookPage.items.length).toBeGreaterThan(0);
  const book = bookPage.items[0];
  const seen: string[] = [];
  let release = () => {};
  const smallGate = new Promise<void>(resolve => { release = resolve; });
  try {
    await page.route('**/api/v1/columns/77/tree', route => route.fulfill({ json: {
      column: { id: 77, name: 'Subjects', datatype: 'text', hierarchical: true },
      nodes: ['Large', 'Small'].map(name => ({ name, path: name, count: name === 'Large' ? 50 : 1,
        total_count: name === 'Large' ? 50 : 1, children: [] })),
    } }));
    await page.route('**/api/v1/columns/77/books?*', async route => {
      const url = new URL(route.request().url());
      const path = url.searchParams.get('path') || '';
      const pageNumber = Number(url.searchParams.get('page'));
      seen.push(`${path}:${pageNumber}`);
      if (path === 'Small') await smallGate;
      await route.fulfill({ json: { items: [{ ...book, title: path === 'Small' ? 'Small node book' : 'Large node book' }],
        total: path === 'Small' ? 1 : 50, page: pageNumber, per_page: 24, path,
        column: { id: 77, name: 'Subjects' } } });
    });
    await page.goto('/app/cc/77?path=Large');
    await expect(page.getByText('Page 1 of 3', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(page.getByText('Page 2 of 3', { exact: true })).toBeVisible();
    await page.getByRole('link', { name: 'Small', exact: true }).click();
    await expect.poll(() => seen.includes('Small:1')).toBe(true);
    await expect(page.getByText('Large node book', { exact: true })).toHaveCount(0);
    expect(seen).not.toContain('Small:2');
    release();
    await expect(page.getByText('Small node book', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Previous', exact: true })).toHaveCount(0);
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('custom-column cards honor the reader cover-action preference', async ({ secondaryUser }) => {
  const page = secondaryUser.page;
  const library = await page.request.get('/api/v1/books?per_page=50').then(r => r.json());
  const book = library.items.find((b: { formats: string[] }) => b.formats.includes('EPUB'));
  expect(book).toBeTruthy();
  await page.route('**/api/v1/columns/77/tree', route => route.fulfill({ json: {
    column: { id: 77, name: 'Subjects', datatype: 'text', hierarchical: false }, nodes: [],
  } }));
  await page.route('**/api/v1/columns/77/books?*', route => route.fulfill({ json: {
    items: [book], total: 1, page: 1, per_page: 24, path: '', column: { id: 77, name: 'Subjects' },
  } }));
  const csrf = await page.request.get('/api/v1/auth/csrf').then(r => r.json());
  const save = async (hidden: boolean) => {
    expect((await page.request.post('/api/v1/account/preferences', {
      headers: { 'X-CSRFToken': csrf.csrf_token }, data: { preferences: { card_actions_hidden: hidden } },
    })).ok()).toBeTruthy();
    await page.goto('/app/cc/77');
  };
  const details = page.getByRole('link', { name: `Open details for ${book.title}`, exact: true });
  const actions = page.getByRole('button', { name: `Actions for ${book.title}`, exact: true });
  await save(false);
  await expect(details).toBeVisible();
  await expect(actions).toHaveCount(1);
  if (test.info().project.use.hasTouch) await actions.tap();
  else { await actions.focus(); await actions.press('Enter'); }
  const dialog = page.getByRole('dialog', { name: `Actions for ${book.title}`, exact: true });
  await expect(dialog).toBeVisible();
  const read = dialog.getByRole('link', { name: 'Read now', exact: true });
  await expect(read).toBeVisible();
  await expect(read).toHaveAttribute('href', `/app/read/${book.id}`);
  if (test.info().project.use.hasTouch) {
    await dialog.getByRole('button', { name: 'Close', exact: true }).tap();
  } else {
    await dialog.press('Escape');
    await expect(actions).toBeFocused();
  }
  await expect(dialog).toHaveCount(0);
  await save(true);
  await expect(actions).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(details).toBeVisible();
});


test('a reader without the viewer role can browse column metadata without a Read action', async ({ page }) => {
  const library = await page.request.get('/api/v1/books?per_page=1').then(r => r.json());
  const book = library.items[0];
  expect(book).toBeTruthy();
  const me = await page.request.get('/api/v1/auth/me').then(r => r.json());
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { ...me, role: { ...me.role, viewer: false } } }));
  await page.route('**/api/v1/columns/77/tree', route => route.fulfill({ json: {
    column: { id: 77, name: 'Subjects', datatype: 'text', hierarchical: false }, nodes: [],
  } }));
  await page.route('**/api/v1/columns/77/books?*', route => route.fulfill({ json: {
    items: [book], total: 1, page: 1, per_page: 24, path: '', column: { id: 77, name: 'Subjects' },
  } }));
  await page.goto('/app/cc/77');
  await expect(page.getByRole('link', { name: `Open details for ${book.title}`, exact: true })).toBeVisible();
  const actions = page.getByRole('button', { name: `Actions for ${book.title}`, exact: true });
  if (test.info().project.use.hasTouch) await actions.tap();
  else { await actions.focus(); await actions.press('Enter'); }
  const dialog = page.getByRole('dialog', { name: `Actions for ${book.title}`, exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('link', { name: 'Read now', exact: true })).toHaveCount(0);
});


test('a stale bookmarked value surfaces its books error with After children selected', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('cwng:cc-books-after-children', '1'));
  await page.route('**/api/v1/columns/77/tree', route => route.fulfill({ json: {
    column: { id: 77, name: 'Subjects', datatype: 'text', hierarchical: true },
    nodes: [{ name: 'Current', path: 'Current', count: 1, total_count: 1, children: [] }],
  } }));
  await page.route('**/api/v1/columns/77/books?*', route => route.fulfill({ status: 404,
    json: { error: { code: 'not_found', message: 'Category not found' } } }));
  await page.goto('/app/cc/77?path=Old.Value');
  await expect(page.getByRole('button', { name: 'After children', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('heading', { name: 'Could not load books', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
});
