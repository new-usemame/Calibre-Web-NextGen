import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #1496 — destructive SPA actions must confirm before they fire.
 *
 * Reported by @JamesHACS: "Reload metadata from disk" sits in the same action
 * row as the per-format download buttons on the book page and fired straight
 * out of onClick. Reaching for a download and landing one button over rewrote
 * the book's details from the file, replacing any title/author/series the user
 * had curated. There is no undo in the UI.
 *
 * The SPA already had a settled convention — bulk merge, bulk delete, format
 * delete, whole-book delete, shelf delete, smart-shelf delete, admin user
 * delete and admin password reset all guard with window.confirm. Reload was the
 * one that didn't, and the audit for this fix found a second: revoking an app
 * password on the Account page, which cuts off whatever device holds it, cannot
 * be recovered, and renders as a list of identical trash buttons where a
 * misclick lands on the wrong row.
 *
 * The load-bearing assertion in each pair is the DISMISS case: a confirm that
 * appears but doesn't gate the mutation would be theatre. Pre-fix both dismiss
 * specs fail twice over — no dialog is raised, and the request goes out anyway.
 * The accept case is the don't-break-the-button guard and passes either way.
 */

/** First book id in the seeded library, or null. */
async function firstBookId(page: Page): Promise<number | null> {
  return page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null))
      .catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
}

/** Count calls to a route and stub them, so "never fired" is directly observable. */
async function stubAndCount(page: Page, glob: string, body: object): Promise<() => number> {
  let calls = 0;
  await page.route(glob, async (route) => {
    calls += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
  return () => calls;
}

const RELOAD_GLOB = '**/admin/book/*/reload_metadata';
const RELOAD_OK = {
  success: true,
  updated_fields: [],
  source_format: 'EPUB',
  message: 'Metadata reloaded',
};

test('dismissing the confirm does NOT reload metadata from disk (#1496)', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  const errors = collectPageErrors(page);
  const reloadCalls = await stubAndCount(page, RELOAD_GLOB, RELOAD_OK);

  let dialogType: string | null = null;
  let dialogMessage = '';
  page.on('dialog', (d) => {
    dialogType = d.type();
    dialogMessage = d.message();
    void d.dismiss();
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });

  const reload = page.getByRole('button', { name: 'Reload metadata from disk' });
  await expect(reload).toBeVisible({ timeout: 10_000 });
  await reload.click();

  // Give a request that shouldn't happen every chance to happen.
  await page.waitForTimeout(1_000);

  expect(dialogType, 'a confirm dialog must be shown before reloading').toBe('confirm');
  // The wording has to say what is lost, not just "are you sure" — this is the
  // whole reason the misclick was expensive.
  expect(dialogMessage).toContain('cannot be undone');
  expect(reloadCalls(), 'dismissing the confirm must not call the reload endpoint').toBe(0);

  assertNoPageErrors(errors);
});

test('accepting the confirm still reloads metadata from disk (#1496)', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  const reloadCalls = await stubAndCount(page, RELOAD_GLOB, RELOAD_OK);
  page.on('dialog', (d) => void d.accept());

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });

  const reload = page.getByRole('button', { name: 'Reload metadata from disk' });
  await expect(reload).toBeVisible({ timeout: 10_000 });

  const [req] = await Promise.all([
    page.waitForRequest(RELOAD_GLOB, { timeout: 10_000 }),
    reload.click(),
  ]);
  expect(req.method()).toBe('POST');
  // waitForRequest resolves when the request is ISSUED, but the counter lives
  // in the route handler, which Playwright invokes afterwards — reading it once
  // here races the handler and reports 0 even though the call was made. The
  // dismiss specs only survive this because of their settle delay.
  await expect.poll(reloadCalls, { timeout: 5_000 }).toBe(1);
});

// ── App-password revoke: same defect class, found by auditing around #1496 ────

const REVOKE_GLOB = '**/api/v1/account/app-passwords/*/delete';
const STUB_LABEL = 'KOReader on phone';

/** Ensure the account payload carries a known app password, so the spec doesn't
 *  depend on the seed having generated one. */
async function withStubbedAppPassword(page: Page) {
  await page.route('**/api/v1/account', async (route) => {
    const res = await route.fetch();
    const account = await res.json();
    account.app_passwords = [{ id: 990501, label: STUB_LABEL }];
    await route.fulfill({ response: res, json: account });
  });
}

test('dismissing the confirm does NOT revoke an app password (#1496)', async ({ page }) => {
  const errors = collectPageErrors(page);
  await withStubbedAppPassword(page);
  const revokeCalls = await stubAndCount(page, REVOKE_GLOB, {});

  let dialogType: string | null = null;
  let dialogMessage = '';
  page.on('dialog', (d) => {
    dialogType = d.type();
    dialogMessage = d.message();
    void d.dismiss();
  });

  await page.goto('/app/account', { waitUntil: 'domcontentloaded' });

  const revoke = page.getByRole('button', { name: `Revoke ${STUB_LABEL}` });
  await expect(revoke).toBeVisible({ timeout: 10_000 });
  await revoke.click();
  await page.waitForTimeout(1_000);

  expect(dialogType, 'a confirm dialog must be shown before revoking').toBe('confirm');
  // Names the credential, so a misclick on the wrong row is caught by reading it.
  expect(dialogMessage).toContain(STUB_LABEL);
  expect(revokeCalls(), 'dismissing the confirm must not call the revoke endpoint').toBe(0);

  // The password is still listed — the row was not optimistically removed.
  await expect(revoke).toBeVisible();

  assertNoPageErrors(errors);
});

test('accepting the confirm still revokes the app password (#1496)', async ({ page }) => {
  await withStubbedAppPassword(page);
  const revokeCalls = await stubAndCount(page, REVOKE_GLOB, {});
  page.on('dialog', (d) => void d.accept());

  await page.goto('/app/account', { waitUntil: 'domcontentloaded' });

  const revoke = page.getByRole('button', { name: `Revoke ${STUB_LABEL}` });
  await expect(revoke).toBeVisible({ timeout: 10_000 });

  const [req] = await Promise.all([
    page.waitForRequest(REVOKE_GLOB, { timeout: 10_000 }),
    revoke.click(),
  ]);
  expect(req.method()).toBe('POST');
  // Same issue-vs-handle race as the reload accept spec above.
  await expect.poll(revokeCalls, { timeout: 5_000 }).toBe(1);
});
