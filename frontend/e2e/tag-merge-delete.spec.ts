import { test, expect, type Page, type TestInfo } from '@playwright/test';

/**
 * #973 — consolidating tags from the all-tags view.
 *
 * The reporter's goal is de-duplication: rename a tag onto its near-duplicate
 * so the two become one. Renaming onto an existing name answered a flat 409
 * ("A tag with that name already exists") with nothing to act on, and there was
 * no delete anywhere. These specs drive the two flows through the real UI —
 * the API contract is unit-tested separately, so what matters here is that the
 * conflict becomes an offer the user can accept and that delete needs a confirm.
 */

type Entity = { id: number; name: string; count: number };

async function tagsFromApi(page: Page): Promise<Entity[]> {
  const res = await page.request.get('/api/v1/tags');
  expect(res.ok(), 'tag list should load').toBeTruthy();
  return ((await res.json()) as { items: Entity[] }).items;
}

/**
 * The server side of #973 ships in the same change as this spec, and the PR leg
 * of the e2e job runs the :dev API with only this branch's SPA overlaid — see
 * "E2E Tests (SPA)" in .github/workflows/tests.yml, which states the limit: it
 * cannot cover a change that needs the PR's own backend. Against that API these
 * routes do not exist, so the flows are unreachable for a reason unrelated to
 * the SPA. Ask the server what it supports rather than assume: Flask answers
 * OPTIONS with the methods registered for the rule. Tag runs test the tag's own
 * image, and :dev carries the routes once this lands, so both run for real —
 * the skip retires itself instead of silently hollowing out the gate.
 */
async function serverHasTagMaintenance(page: Page): Promise<boolean> {
  const res = await page.request.fetch('/api/v1/tags/1', { method: 'OPTIONS' });
  return (res.headers()['allow'] ?? '').toUpperCase().includes('DELETE');
}

/**
 * Every test here destroys a tag, so none of them can borrow a seed row and put
 * it back the way tag-rename.spec.ts does — a merged or deleted tag cannot be
 * handed back. Each test mints tags of its own instead, on books that no other
 * project is writing to, and restores those books' tag lists afterwards.
 *
 * Tags exist only as book metadata, so minting one means writing a book. That
 * write is read-modify-write, which makes the BOOK the contended resource: the
 * desktop and mobile projects run concurrently against one server, so they get
 * separate books. Within a project this file runs serially (below), so one pair
 * of books covers all three tests.
 */
const BOOKS_PER_LANE = 2;

async function laneBooks(page: Page, testInfo: TestInfo): Promise<[number, number]> {
  const res = await page.request.get('/api/v1/books?per_page=12');
  expect(res.ok(), 'book list should load').toBeTruthy();
  const ids = ((await res.json()) as { items: { id: number }[] }).items.map((b) => b.id);
  const base = (testInfo.project.name === 'mobile' ? 1 : 0) * BOOKS_PER_LANE;
  expect(ids.length, 'each project needs its own books to write tags onto')
    .toBeGreaterThanOrEqual(base + BOOKS_PER_LANE);
  return [ids[base], ids[base + 1]];
}

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function bookTags(page: Page, id: number): Promise<string> {
  const res = await page.request.get(`/api/v1/books/${id}/metadata`);
  expect(res.ok(), `metadata for book ${id} should load`).toBeTruthy();
  return ((await res.json()) as { tags: string }).tags ?? '';
}

async function setBookTags(page: Page, id: number, tags: string): Promise<void> {
  const res = await page.request.post(`/api/v1/books/${id}/metadata`, {
    headers: { 'X-CSRFToken': await csrfToken(page) },
    data: { tags },
  });
  expect(res.ok(), `tags should save to book ${id}`).toBeTruthy();
}

/** Put a fresh tag on a book and hand back the undo. */
async function mintTag(page: Page, bookId: number, name: string): Promise<() => Promise<void>> {
  const before = await bookTags(page, bookId);
  await setBookTags(page, bookId, before ? `${before}, ${name}` : name);
  const seen = await tagsFromApi(page);
  expect(seen.map((t) => t.name), `minted tag ${name} should exist`).toContain(name);
  return async () => { await setBookTags(page, bookId, before); };
}

/** Unique per project and per run, so nothing collides with a parallel lane. */
function tagName(testInfo: TestInfo, role: string): string {
  return `e2e-973-${role}-${testInfo.project.name}-${Date.now()}`;
}

function tagRow(page: Page, name: string) {
  return page.getByRole('listitem').filter({ hasText: name }).first();
}

// Serial: these share one pair of books, and a merge or delete leaves the tag
// list in a different shape than the next test read it in.
test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await page.goto('/app/tags');
  test.skip(!(await serverHasTagMaintenance(page)),
    'server predates #973 (the PR e2e leg runs the :dev API with this branch\'s SPA)');
});

test('a rename collision offers to merge instead of dead-ending', async ({ page }, testInfo) => {
  const [bookA, bookB] = await laneBooks(page, testInfo);
  const sourceName = tagName(testInfo, 'source');
  const targetName = tagName(testInfo, 'target');
  const undo = [await mintTag(page, bookA, sourceName), await mintTag(page, bookB, targetName)];

  try {
    await page.goto('/app/tags');
    await tagRow(page, sourceName).getByRole('button', { name: `Rename tag ${sourceName}` }).click();

    const input = page.getByRole('textbox', { name: 'Tag name' });
    await input.fill(targetName);
    await input.press('Enter');

    // The whole point of the fix: the collision names the other tag and its book
    // count, and offers the merge. A bare error string is the pre-fix behaviour.
    const prompt = page.getByRole('alert').filter({ hasText: targetName });
    await expect(prompt).toContainText('already exists on 1 book');
    await expect(page.getByRole('button', { name: `Merge into ${targetName}` })).toBeVisible();

    // Cancelling must leave the library untouched.
    await prompt.getByRole('button', { name: 'Cancel' }).click();
    const after = await tagsFromApi(page);
    expect(after.map((t) => t.name), 'the tag survives a cancelled merge').toContain(sourceName);
    expect(after.find((t) => t.name === targetName)?.count,
      'and the other tag keeps its books').toBe(1);
  } finally {
    for (const restore of undo) await restore();
  }
});

test('merging folds one tag into the other and the list loses a row', async ({ page }, testInfo) => {
  const [bookA, bookB] = await laneBooks(page, testInfo);
  const sourceName = tagName(testInfo, 'source');
  const targetName = tagName(testInfo, 'target');
  const undo = [await mintTag(page, bookA, sourceName), await mintTag(page, bookB, targetName)];

  try {
    await page.goto('/app/tags');
    await tagRow(page, sourceName).getByRole('button', { name: `Rename tag ${sourceName}` }).click();
    const input = page.getByRole('textbox', { name: 'Tag name' });
    await input.fill(targetName);
    await input.press('Enter');

    const merged = page.waitForResponse((r) =>
      r.url().includes('/api/v1/tags/') && r.request().method() === 'POST' && r.status() === 200);
    await page.getByRole('button', { name: `Merge into ${targetName}` }).click();
    expect(((await (await merged).json()) as { merged?: boolean }).merged).toBe(true);

    const after = await tagsFromApi(page);
    expect(after.map((t) => t.name), 'the folded tag is gone').not.toContain(sourceName);
    // Each tag started on one book; the survivor now carries both.
    expect(after.find((t) => t.name === targetName)?.count,
      'the survivor absorbed the other tag\'s book').toBe(2);
  } finally {
    for (const restore of undo) await restore();
  }
});

test('deleting a tag takes a confirm, then removes only the tag', async ({ page }, testInfo) => {
  const [bookA] = await laneBooks(page, testInfo);
  const victimName = tagName(testInfo, 'victim');
  const undo = await mintTag(page, bookA, victimName);

  try {
    const booksRes = await page.request.get('/api/v1/books?per_page=1');
    const totalBooksBefore = ((await booksRes.json()) as { total: number }).total;

    await page.goto('/app/tags');
    await tagRow(page, victimName).getByRole('button', { name: `Delete tag ${victimName}` }).click();

    // One click must not destroy anything — the confirm names the tag first.
    await expect(page.getByRole('alert').filter({ hasText: victimName })).toBeVisible();
    expect((await tagsFromApi(page)).map((t) => t.name),
      'nothing is removed until the confirm').toContain(victimName);

    const deleted = page.waitForResponse((r) =>
      r.url().includes('/api/v1/tags/') && r.request().method() === 'DELETE' && r.status() === 200);
    await page.getByRole('button', { name: `Confirm delete tag ${victimName}` }).click();
    await deleted;

    expect((await tagsFromApi(page)).map((t) => t.name)).not.toContain(victimName);
    const booksAfter = await page.request.get('/api/v1/books?per_page=1');
    expect(((await booksAfter.json()) as { total: number }).total,
      'deleting a tag must not delete books').toBe(totalBooksBefore);
  } finally {
    await undo();
  }
});
