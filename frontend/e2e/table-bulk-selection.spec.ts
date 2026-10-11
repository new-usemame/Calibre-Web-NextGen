import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow } from './utils';
import type { Book } from '../src/lib/api';

// Mounted Table, selection hooks, BulkBar and metadata transport with isolated
// synthetic books and write responses. No shared CI books are modified.
const IDS = [990101, 990102, 990103, 990104, 990105, 990106];
const title = (id: number) => `Table selection ${id}`;
const checkbox = (page: Page, id: number) => page.getByRole('checkbox', { name: `Select ${title(id)}`, exact: true });
const row = (page: Page, id: number) => page.getByRole('row').filter({ has: page.locator(`a[href$="/book/${id}"]`) });

async function fixture(page: Page, options: { editable?: boolean; personal?: boolean; holdAll?: Promise<void>; theme?: string } = {}) {
  const tags = new Map<number, string[]>();
  const writes: { id: number; body: Record<string, unknown> }[] = [];
  const selectedScopes: string[] = [];
  const removed = new Set<number>();
  const membershipWrites: number[][] = [];
  let membershipAttempt = 0;
  let rejectLast = true;
  let editable = options.editable !== false;
  let releaseWrites!: () => void;
  const pendingWrites = new Promise<void>((resolve) => { releaseWrites = resolve; });
  await page.addInitScript(() => {
    class NeverIntersectingObserver { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
    window.IntersectionObserver = NeverIntersectingObserver as unknown as typeof IntersectionObserver;
  });
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch(); const me = await response.json();
    me.role = { ...me.role, edit: editable, delete_books: false, anonymous: false };
    if (options.personal) me.library_mode = 'personal_library';
    if (options.theme) me.theme = options.theme;
    await route.fulfill({ response, json: me });
  });
  await page.route('**/api/v1/books?**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.has('select_all')) {
      selectedScopes.push(url.searchParams.get('sort') ?? ''); await options.holdAll;
      return route.fulfill({ json: { ids: IDS, total: IDS.length } });
    }
    const number = Number(url.searchParams.get('page') ?? 1);
    const remaining = IDS.filter((id) => !removed.has(id));
    const ordered = url.searchParams.get('sort') === 'abc' ? [...remaining].reverse() : remaining;
    const items: Book[] = ordered.slice((number - 1) * 3, number * 3).map((id) => ({
      id, title: title(id), authors: ['Fixture Author'], series: null, series_index: null,
      cover_url: null, formats: [], tags: tags.get(id) ?? ['Original'], read: false, archived: false,
    }));
    await route.fulfill({ json: { items, page: number, per_page: 3, total: ordered.length } });
  });
  await page.route('**/api/v1/books/*/metadata', async (route) => {
    const path = new URL(route.request().url()).pathname.split('/');
    const id = Number(path[path.length - 2]);
    const body = route.request().postDataJSON() as Record<string, unknown>; writes.push({ id, body });
    await pendingWrites;
    if (rejectLast && id === IDS[5]) return route.fulfill({ json: { id, errors: { tags: 'Synthetic rejected tag' } } });
    tags.set(id, ['Original', String(body.tags)]);
    await route.fulfill({ json: { id, title: title(id), errors: {} } });
  });
  await page.route('**/api/v1/books/my-library/batch', async (route) => {
    const body = route.request().postDataJSON() as { operation: string; book_ids: number[] };
    expect(body.operation).toBe('remove');
    membershipWrites.push(body.book_ids);
    const failed = membershipAttempt === 0 ? body.book_ids.slice(-1) : membershipAttempt === 1 ? body.book_ids : [];
    membershipAttempt += 1;
    const succeeded = body.book_ids.filter((id) => !failed.includes(id));
    succeeded.forEach((id) => removed.add(id));
    await route.fulfill({ json: {
      succeeded_ids: succeeded, failed_ids: failed,
      results: body.book_ids.map((id) => failed.includes(id)
        ? { book_id: id, status: 'failed', error: { code: 'fixture_rejection', message: 'Synthetic membership rejection' } }
        : { book_id: id, status: 'succeeded' }),
    } });
  });
  return { writes, membershipWrites, selectedScopes, releaseWrites, revokeEdit: () => { editable = false; }, acceptLast: () => { rejectLast = false; } };
}

test('table ranges and complete selection edit beyond loaded rows and retain only failed IDs', async ({ page }) => {
  const data = await fixture(page); const errors = collectPageErrors(page);
  try {
    await page.goto('/app/table'); await expect(row(page, IDS[0])).toBeVisible();
    await expect(page.getByRole('checkbox', { name: /^Select Table selection / })).toHaveCount(0);
    await page.getByRole('button', { name: `Edit title for ${title(IDS[0])}`, exact: true }).click();
    await page.getByRole('textbox', { name: 'Title', exact: true }).fill('Uncommitted title');
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    await expect(page.getByRole('textbox', { name: 'Title', exact: true })).toHaveCount(0);
    expect(data.writes).toEqual([]);
    await checkbox(page, IDS[0]).focus(); await page.keyboard.press('Space');
    await checkbox(page, IDS[2]).click({ modifiers: ['Shift'] });
    await expect(page.getByRole('region', { name: '3 selected', exact: true })).toBeVisible();
    await checkbox(page, IDS[0]).click({ modifiers: ['Shift'] });
    for (const id of IDS.slice(0, 3)) await expect(checkbox(page, id)).not.toBeChecked();
    await page.getByRole('button', { name: 'Select all 6 books', exact: true }).click();
    await expect(page.getByRole('region', { name: '6 selected', exact: true })).toBeVisible();
    expect(data.selectedScopes).toEqual(['new']);
    await expect(page.getByRole('checkbox', { name: /^Select Table selection / })).toHaveCount(3);
    await page.getByRole('button', { name: 'Edit metadata', exact: true }).click();
    await page.getByRole('textbox', { name: 'Tags (comma separated)', exact: true }).fill('Table changed');
    await page.getByRole('button', { name: 'Apply to 6 books', exact: true }).click();
    await expect.poll(() => data.writes.length).toBeGreaterThan(0);
    await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeDisabled();
    await expect(checkbox(page, IDS[0])).toBeDisabled(); data.releaseWrites();
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).toBeVisible();
    expect(data.writes.map((write) => write.id).sort()).toEqual(IDS);
    expect(data.writes.every((write) => write.body.tags === 'Table changed' && write.body.list_mode === 'add')).toBe(true);
    await expect(row(page, IDS[0])).toContainText('Original, Table changed');
    await page.getByRole('button', { name: 'Edit metadata', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Apply metadata', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Load more', exact: true }).click();
    await expect(checkbox(page, IDS[5])).toBeChecked();
    await expect(row(page, IDS[5])).toContainText('Original');
    expect(await row(page, IDS[5]).textContent()).not.toContain('Table changed'); data.acceptLast();
    await page.getByRole('button', { name: 'Edit metadata', exact: true }).click();
    await expect(page.getByRole('textbox', { name: 'Tags (comma separated)', exact: true })).toHaveValue('Table changed');
    await page.getByRole('button', { name: 'Apply to 1 books', exact: true }).click();
    await expect.poll(() => data.writes.length).toBe(7); expect(data.writes[6].id).toBe(IDS[5]);
    await expect(page.getByRole('region', { name: 'Apply metadata', exact: true })).toHaveCount(0);
    await expect(page.getByRole('checkbox', { name: /^Select Table selection / })).toHaveCount(3);
    await expect(row(page, IDS[0])).toBeVisible();
    await expect(row(page, IDS[5])).toHaveCount(0);
    expect(await page.locator('tbody a').allTextContents()).toEqual(IDS.slice(0, 3).map(title));
    await page.getByRole('button', { name: 'Load more', exact: true }).click();
    await expect(row(page, IDS[5])).toContainText('Original, Table changed');
    await assertNoHorizontalOverflow(page);
    const axe = await new AxeBuilder({ page }).include('main').analyze();
    expect(axe.violations.filter((issue) => ['critical', 'serious'].includes(issue.impact ?? ''))).toEqual([]);
    await page.screenshot({ path: test.info().outputPath('table-selected.jpg'), type: 'jpeg', quality: 75 });
    await page.getByRole('button', { name: 'Clear selection', exact: true }).focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeFocused(); assertNoPageErrors(errors);
  } finally { data.releaseWrites(); }
});

test('sorting cancels pending complete selection; reader permissions hide metadata actions', async ({ page }) => {
  let release!: () => void; const pending = new Promise<void>((resolve) => { release = resolve; });
  const data = await fixture(page, { editable: false, personal: true, holdAll: pending });
  const errors = collectPageErrors(page);
  try {
    await page.goto('/app/table'); await expect(row(page, IDS[0])).toBeVisible();
    await page.getByRole('button', { name: 'Select', exact: true }).click(); await checkbox(page, IDS[0]).check();
    await expect(page.getByRole('button', { name: 'Remove from my library', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Edit metadata', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Delete', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Select all 6 books', exact: true }).click();
    await expect.poll(() => data.selectedScopes.length).toBe(1);
    await page.getByRole('columnheader', { name: 'Title', exact: true }).getByRole('button').click();
    await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeVisible(); await expect(row(page, IDS[5])).toBeVisible();
    const received = page.waitForResponse((response) => response.url().includes('select_all=1'));
    release(); await (await received).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByRole('region', { name: /^\d+ selected$/ })).toHaveCount(0);
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    for (const id of IDS.slice(3)) await expect(checkbox(page, id)).not.toBeChecked();
    await expect(page.getByRole('button', { name: /^Edit title for / })).toHaveCount(0);
    expect(data.writes).toEqual([]); assertNoPageErrors(errors);
  } finally { release(); data.releaseWrites(); }
});

// Same hook-level membership refresh as production: revision changes BEFORE
// the per-call success handler reports failed IDs. No shared book is changed.
for (const theme of ['light', 'dark']) {
  test(`table membership partial and all-failed retries survive refresh (${theme})`, async ({ page }) => {
    const data = await fixture(page, { editable: false, personal: true, theme });
    const errors = collectPageErrors(page);
    page.on('dialog', (dialog) => { void dialog.accept(); });
    try {
      await page.goto('/app/table'); await expect(row(page, IDS[0])).toBeVisible();
      await page.getByRole('button', { name: 'Select', exact: true }).click();
      await checkbox(page, IDS[0]).check(); await checkbox(page, IDS[2]).click({ modifiers: ['Shift'] });
      await page.getByRole('button', { name: 'Remove from my library', exact: true }).click();
      await expect(page.getByRole('region', { name: '1 selected', exact: true })).toBeVisible();
      await expect(page.locator('[aria-live="assertive"]')).toContainText('2 book(s) removed from your library; 1 failed.');
      await expect(checkbox(page, IDS[2])).toBeChecked();
      await expect(row(page, IDS[0])).toHaveCount(0); await expect(row(page, IDS[1])).toHaveCount(0);
      expect(data.membershipWrites).toEqual([IDS.slice(0, 3)]);
      await page.getByRole('button', { name: 'Remove from my library', exact: true }).click();
      await expect(page.locator('[aria-live="assertive"]')).toContainText('0 book(s) removed from your library; 1 failed.');
      await expect(checkbox(page, IDS[2])).toBeChecked();
      expect(data.membershipWrites).toEqual([IDS.slice(0, 3), [IDS[2]]]);
      await assertNoHorizontalOverflow(page);
      const axe = await new AxeBuilder({ page }).include('main').analyze();
      expect(axe.violations.filter((issue) => ['critical', 'serious'].includes(issue.impact ?? ''))).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`table-${theme}-retry.jpg`), type: 'jpeg', quality: 75 });
      await page.getByRole('button', { name: 'Remove from my library', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeFocused();
      await expect(page.getByRole('region', { name: /^\d+ selected$/ })).toHaveCount(0);
      await expect(row(page, IDS[2])).toHaveCount(0);
      expect(data.membershipWrites).toEqual([IDS.slice(0, 3), [IDS[2]], [IDS[2]]]);
      await page.getByRole('button', { name: 'Select', exact: true }).click();
      await page.getByRole('button', { name: 'Done', exact: true }).focus(); await page.keyboard.press('Enter');
      await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeFocused();
      assertNoPageErrors(errors);
    } finally { data.releaseWrites(); }
  });
}

test('live permission revocation closes selected metadata drafts', async ({ page }) => {
  const data = await fixture(page, { personal: true }); const errors = collectPageErrors(page);
  page.on('dialog', (dialog) => { void dialog.accept(); });
  try {
    await page.goto('/app/table'); await expect(row(page, IDS[0])).toBeVisible();
    await page.getByRole('button', { name: 'Select', exact: true }).click(); await checkbox(page, IDS[0]).check();
    await page.getByRole('button', { name: 'Edit metadata', exact: true }).click();
    await page.getByRole('textbox', { name: 'Tags (comma separated)', exact: true }).fill('Uncommitted bulk draft');
    data.revokeEdit();
    // Removal refreshes the real me query as well as the library revision.
    await page.getByRole('button', { name: 'Remove from my library', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Tags (comma separated)', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Edit title for / })).toHaveCount(0);
    await page.getByRole('button', { name: 'Select', exact: true }).click(); await checkbox(page, IDS[0]).check();
    await expect(page.getByRole('button', { name: 'Edit metadata', exact: true })).toHaveCount(0);
    expect(data.writes).toEqual([]); assertNoPageErrors(errors);
  } finally { data.releaseWrites(); }
});
