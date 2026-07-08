import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow } from './utils';

/*
 * Customize sidebar — drag-reorder + keyboard reorder (the drag-polish rebuild).
 *
 * The contract this pins, end-to-end through the real app (not the isolated
 * harness): pressing a row and dragging it past its neighbours reorders the
 * list; keyboard arrows on the focused handle move a row; and pressing Done
 * persists the new order across a reload. The old implementation reordered via
 * elementFromPoint with no lift and jittered at boundaries — this asserts the
 * user-visible outcome (order changes correctly), which that approach could not
 * guarantee deterministically.
 */

// Mobile: the rail is a drawer behind a hamburger; open it if present. Desktop:
// the rail is always visible, so this is a no-op. Safe to call after a reload,
// where the mobile drawer resets to closed.
async function ensureSidebarVisible(page: Page) {
  const burger = page.getByRole('button', { name: /open navigation/i });
  if (await burger.isVisible().catch(() => false)) await burger.click();
}

async function openSidebar(page: Page) {
  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
  await ensureSidebarVisible(page);
}

async function enterEdit(page: Page) {
  await page.getByRole('button', { name: 'Customize sidebar' }).click();
  // 13 orderable rows become draggable handles once edit mode is live.
  await expect(page.locator('nav li[data-key]')).toHaveCount(13);
}

function keys(page: Page): Promise<string[]> {
  return page.locator('nav li[data-key]').evaluateAll((els) =>
    els.map((e) => e.getAttribute('data-key') || ''),
  );
}

// A realistic multi-step pointer drag (down/glide/up) so it exercises the same
// path a finger or mouse does, not an instantaneous jump.
async function dragRow(page: Page, key: string, toKey: string, place: 'above' | 'below') {
  const from = await page.locator(`nav li[data-key="${key}"]`).boundingBox();
  const to = await page.locator(`nav li[data-key="${toKey}"]`).boundingBox();
  if (!from || !to) throw new Error('row not visible');
  const x = from.x + from.width / 2;
  const startY = from.y + from.height / 2;
  const endY = place === 'below' ? to.y + to.height : to.y;
  await page.mouse.move(x, startY);
  await page.mouse.down();
  const steps = 14;
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(x, startY + ((endY - startY) * i) / steps);
    await page.waitForTimeout(12);
  }
  await page.mouse.up();
  await page.waitForTimeout(350); // FLIP settle
}

test.describe('customize sidebar reorder', () => {
  test('drag reorders, keyboard reorders, Done persists across reload', async ({ page }) => {
    const errors = collectPageErrors(page);
    await openSidebar(page);
    await enterEdit(page);

    // Normalize to default first — this is a shared account and a prior run (or a
    // failed run whose cleanup was skipped) may have left it reordered. Reset sets
    // the edit state to the default order locally, giving a known start point.
    await page.getByRole('button', { name: 'Reset to default' }).click();
    await page.waitForTimeout(200);
    const initial = await keys(page);
    expect(initial[0], 'reset restores author to the top').toBe('author');

    // ── drag: author (top) down past publisher → lands mid-list, order changes ──
    await dragRow(page, 'author', 'publisher', 'below');
    const afterDrag = await keys(page);
    expect(afterDrag, 'drag changed the order').not.toEqual(initial);
    expect(afterDrag[0], 'author left the top slot').not.toBe('author');
    expect(afterDrag, 'no row lost or duplicated').toHaveLength(13);
    expect([...afterDrag].sort(), 'same set of rows').toEqual([...initial].sort());

    // ── keyboard: focus a handle and ArrowUp moves that row up one ──
    const target = afterDrag[4];
    const handle = page.locator(`nav li[data-key="${target}"] button`).first();
    await handle.focus();
    await page.keyboard.press('ArrowUp');
    const afterKey = await keys(page);
    expect(afterKey.indexOf(target), 'ArrowUp moved the row up one slot').toBe(3);

    await assertNoHorizontalOverflow(page);

    // ── persist: Done, reload, order survives ──
    const saved = await keys(page);
    await page.getByRole('button', { name: 'Done' }).click();
    await page.waitForTimeout(300);
    await page.reload();
    await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
    await ensureSidebarVisible(page); // mobile drawer resets to closed on reload
    await enterEdit(page);
    const reloaded = await keys(page);
    expect(reloaded, 'saved order persisted across reload').toEqual(saved);

    // ── cleanup: restore default order so we don't leave the account reordered ──
    await page.getByRole('button', { name: 'Reset to default' }).click();
    await page.getByRole('button', { name: 'Done' }).click();
    await page.waitForTimeout(300);

    assertNoPageErrors(errors);
  });
});
