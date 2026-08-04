import { test, expect, type Page } from '@playwright/test';

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

test('a rename collision offers to merge instead of dead-ending', async ({ page }) => {
  await page.goto('/app/tags');
  const tags = await tagsFromApi(page);
  test.skip(tags.length < 2, 'needs at least two tags to collide');
  const [source, target] = tags;

  const row = page.getByRole('listitem').filter({ hasText: source.name }).first();
  await row.getByRole('button', { name: `Rename tag ${source.name}` }).click();

  const input = page.getByRole('textbox', { name: 'Tag name' });
  await input.fill(target.name);
  await input.press('Enter');

  // The whole point of the fix: the collision names the other tag and its book
  // count, and offers the merge. A bare error string is the pre-fix behaviour.
  const prompt = page.getByRole('alert').filter({ hasText: target.name });
  await expect(prompt).toContainText(`${target.count}`);
  await expect(page.getByRole('button', { name: `Merge into ${target.name}` })).toBeVisible();

  // Cancelling must leave the library untouched.
  await prompt.getByRole('button', { name: 'Cancel' }).click();
  expect((await tagsFromApi(page)).map((t) => t.name)).toContain(source.name);
});

test('merging folds one tag into the other and the list loses a row', async ({ page }) => {
  await page.goto('/app/tags');
  const before = await tagsFromApi(page);
  test.skip(before.length < 2, 'needs at least two tags to merge');
  const [source, target] = before;

  const row = page.getByRole('listitem').filter({ hasText: source.name }).first();
  await row.getByRole('button', { name: `Rename tag ${source.name}` }).click();
  const input = page.getByRole('textbox', { name: 'Tag name' });
  await input.fill(target.name);
  await input.press('Enter');

  const merged = page.waitForResponse((r) =>
    r.url().includes('/api/v1/tags/') && r.request().method() === 'POST' && r.status() === 200);
  await page.getByRole('button', { name: `Merge into ${target.name}` }).click();
  expect(((await (await merged).json()) as { merged?: boolean }).merged).toBe(true);

  const after = await tagsFromApi(page);
  expect(after.map((t) => t.name), 'the folded tag is gone').not.toContain(source.name);
  const survivor = after.find((t) => t.id === target.id)!;
  expect(survivor.count, 'the survivor absorbed the books').toBeGreaterThanOrEqual(target.count);
});

test('deleting a tag takes a confirm, then removes only the tag', async ({ page }) => {
  await page.goto('/app/tags');
  const before = await tagsFromApi(page);
  test.skip(before.length < 1, 'needs a tag to delete');
  const victim = before[before.length - 1];

  const booksRes = await page.request.get('/api/v1/books?limit=1');
  const totalBooksBefore = ((await booksRes.json()) as { total: number }).total;

  const row = page.getByRole('listitem').filter({ hasText: victim.name }).first();
  await row.getByRole('button', { name: `Delete tag ${victim.name}` }).click();

  // One click must not destroy anything — the confirm names the tag first.
  await expect(page.getByRole('alert').filter({ hasText: victim.name })).toBeVisible();
  expect((await tagsFromApi(page)).map((t) => t.id)).toContain(victim.id);

  const deleted = page.waitForResponse((r) =>
    r.url().includes('/api/v1/tags/') && r.request().method() === 'DELETE' && r.status() === 200);
  await page.getByRole('button', { name: `Confirm delete tag ${victim.name}` }).click();
  await deleted;

  expect((await tagsFromApi(page)).map((t) => t.id)).not.toContain(victim.id);
  const booksAfter = await page.request.get('/api/v1/books?limit=1');
  expect(((await booksAfter.json()) as { total: number }).total,
    'deleting a tag must not delete books').toBe(totalBooksBefore);
});
