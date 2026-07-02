import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/*
 * Automated accessibility gate. The A11Y-AUDIT (2026-06-28) was a static source
 * review with known-open findings. This gate FAILS on any 'critical' rule that
 * is NOT in KNOWN_CRITICAL — so it catches NEW critical regressions immediately,
 * while the known backlog is tracked as debt-with-a-name (Class 9: quarantine is
 * a named debt, never a silenced red). Shrink KNOWN_CRITICAL as the audit's
 * findings land — the goal is an empty allowlist.
 */
const FAIL_IMPACTS = ['critical'];

// Known pre-existing critical violations (tracked in notes/A11Y-AUDIT-spa-*.md).
// Remove a rule id here the moment its fix ships so a regression re-fails.
const KNOWN_CRITICAL = new Set<string>([
  'button-name', // icon buttons (BookCard actions, password reveal, etc.) lack discernible text
]);

async function scan(page: Page, url: string, label: string) {
  await page.goto(url);
  // Deterministic scan: wait for the real content, not a mid-render frame
  // (an unstable node count is a flaky gate).
  await page.locator('a[href*="/book/"]').first().waitFor({ state: 'visible' }).catch(() => {});
  await page.waitForLoadState('networkidle');

  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  const impactCount = (i: string) => results.violations.filter((v) => v.impact === i).length;
  test.info().annotations.push({
    type: 'axe',
    description: `${label} — critical:${impactCount('critical')} serious:${impactCount('serious')} moderate:${impactCount('moderate')}`,
  });

  const newCritical = results.violations
    .filter((v) => FAIL_IMPACTS.includes(v.impact || ''))
    .filter((v) => !KNOWN_CRITICAL.has(v.id));
  expect(
    newCritical.map((v) => v.id),
    `NEW critical a11y violations on ${label} (not in KNOWN_CRITICAL):\n${newCritical.map((v) => `  ${v.id}: ${v.help}`).join('\n')}`,
  ).toEqual([]);
}

test('grid has no new critical a11y violations', async ({ page }) => {
  await scan(page, '/app', 'grid');
});

test('book detail has no new critical a11y violations', async ({ page }) => {
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().click();
  await expect(page).toHaveURL(/\/book\/\d+/);
  await scan(page, page.url(), 'book-detail');
});
