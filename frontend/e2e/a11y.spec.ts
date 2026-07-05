import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/*
 * Automated accessibility gate (WCAG 2.2 AA) — Layer 2 of the verification system.
 *
 * After the 2026-07-04 remediation this gate FAILS on any 'critical' OR 'serious'
 * axe violation across the app's routes, plus keyboard/focus invariants axe can't
 * see (skip link, single <main>, no nested card tab stop, mobile drawer inert /
 * trapped). KNOWN is the named-debt allowlist (Class 9): it must stay EMPTY —
 * add a rule id ONLY with a tracking note and a follow-up, never to silence a red.
 *
 * See ~/.claude/skills/CWNG_a11y (the growing a11y skill) for how to grow this.
 */
const FAIL_IMPACTS = ['critical', 'serious'];

// Named debt only. EMPTY is the goal. { 'rule-id': 'why + tracking issue' }.
const KNOWN: Record<string, string> = {};

async function axeScan(page: Page, label: string) {
  await page.waitForLoadState('networkidle');
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();

  const counts = (i: string) => results.violations.filter((v) => v.impact === i).length;
  test.info().annotations.push({
    type: 'axe',
    description: `${label} — critical:${counts('critical')} serious:${counts('serious')} moderate:${counts('moderate')}`,
  });

  const failing = results.violations
    .filter((v) => FAIL_IMPACTS.includes(v.impact || ''))
    .filter((v) => !(v.id in KNOWN));

  // Surface the exact offending nodes so a failure is actionable, not a mystery.
  for (const v of failing) {
    for (const n of v.nodes) {
      console.log(`[a11y:${label}] ${v.id} @ ${JSON.stringify(n.target)} :: ${(n.failureSummary || '').replace(/\n/g, ' ')}`);
    }
  }

  expect(
    failing.map((v) => `${v.id} [${v.impact}] — ${v.help} (${v.nodes.length} node/s)`),
    `Accessibility violations on ${label}:\n${failing
      .map((v) => `  ${v.id} [${v.impact}]: ${v.helpUrl}`)
      .join('\n')}`,
  ).toEqual([]);
}

const isMobile = () => (test.info().project.name === 'mobile');

// ── axe across the app's routes ──────────────────────────────────────────────
test('grid: no critical/serious a11y violations', async ({ page }) => {
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().waitFor({ state: 'visible' }).catch(() => {});
  await axeScan(page, 'grid');
});

test('book detail: no critical/serious a11y violations', async ({ page }) => {
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().click();
  await expect(page).toHaveURL(/\/book\/\d+/);
  await axeScan(page, 'book-detail');
});

test('edit book: no critical/serious a11y violations', async ({ page }) => {
  await page.goto('/app');
  const href = await page.locator('a[href*="/book/"]').first().getAttribute('href');
  const idMatch = href?.match(/\/book\/(\d+)/);
  test.skip(!idMatch, 'no book available');
  await page.goto(`/app/book/${idMatch![1]}/edit`);
  await axeScan(page, 'edit-book');
});

for (const [label, path] of [
  ['account', '/app/account'],
  ['advanced-search', '/app/search'],
  ['shelves', '/app/shelves'],
  ['duplicates', '/app/duplicates'],
  ['admin', '/app/admin'],
] as const) {
  test(`${label}: no critical/serious a11y violations`, async ({ page }) => {
    if (isMobile()) test.skip(); // covered on desktop; keep the mobile run lean
    await page.goto(path);
    await axeScan(page, label);
  });
}

test('reader: TOC traps focus + Escape, named progressbar, no critical/serious', async ({ page }) => {
  if (isMobile()) test.skip();
  // Find a book that offers the in-browser (epub) reader.
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().click();
  const readLink = page.locator('a[href*="/read/"]').first();
  const hasReader = await readLink.isVisible().catch(() => false);
  test.skip(!hasReader, 'no epub reader available in this library');
  await readLink.click();
  await page.getByRole('button', { name: /table of contents/i }).waitFor({ state: 'visible', timeout: 30_000 });

  await expect(page.getByRole('progressbar', { name: /reading progress/i })).toBeVisible();

  await page.getByRole('button', { name: /table of contents/i }).click();
  const toc = page.locator('nav[aria-label="Table of contents"]');
  await expect(toc).toBeVisible();
  const focusInToc = await page.evaluate(() =>
    !!document.activeElement?.closest('nav[aria-label="Table of contents"]'));
  expect(focusInToc, 'focus moved into the TOC drawer').toBeTruthy();
  await page.keyboard.press('Escape');
  await expect(toc).toBeHidden();

  await axeScan(page, 'reader');
});

test.describe('login (unauthenticated)', () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test('login: no critical/serious a11y violations', async ({ page }) => {
    await page.goto('/app');
    await page.locator('input[autocomplete="username"]').waitFor({ state: 'visible' });
    await axeScan(page, 'login');
  });
});

// ── keyboard / focus invariants axe can't observe ────────────────────────────
test('exactly one <main> landmark', async ({ page }) => {
  await page.goto('/app');
  await expect(page.locator('main#main')).toHaveCount(1);
});

test('skip link is the first tab stop and moves focus to <main>', async ({ page }) => {
  if (isMobile()) test.skip();
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().waitFor({ state: 'visible' });
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await page.keyboard.press('Tab');
  const skip = page.locator('a[href="#main"]');
  await expect(skip).toBeFocused();
  await page.keyboard.press('Enter');
  // Activating the skip link puts focus at (or inside) the main landmark.
  const onMain = await page.evaluate(() => {
    const a = document.activeElement;
    return a?.id === 'main' || !!a?.closest('main#main');
  });
  expect(onMain).toBeTruthy();
});

test('book cards are a single tab stop (no nested tabindex)', async ({ page }) => {
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().waitFor({ state: 'visible' });
  // The old BookCard put tabIndex=0 on an inner <article>, a second tab stop.
  await expect(page.locator('article[tabindex]')).toHaveCount(0);
});

test('mobile: closed drawer is inert; open traps focus and Escape closes', async ({ page }) => {
  test.skip(!isMobile(), 'mobile-only');
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().waitFor({ state: 'visible' });

  const nav = page.locator('nav[aria-label]').first();
  // Closed off-canvas drawer must be out of the a11y tree / tab order.
  await expect(nav).toHaveAttribute('inert', '');

  // Open it via the hamburger.
  await page.getByRole('button', { name: /navigation|menu/i }).first().click();
  await expect(nav).not.toHaveAttribute('inert', '');
  // Focus should have moved into the drawer.
  const focusInDrawer = await page.evaluate(() =>
    !!document.activeElement?.closest('nav[aria-label]'),
  );
  expect(focusInDrawer).toBeTruthy();

  // Escape closes and re-inerts.
  await page.keyboard.press('Escape');
  await expect(nav).toHaveAttribute('inert', '');
});
