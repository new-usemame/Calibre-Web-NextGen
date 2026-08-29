import { test, expect, type Locator } from '@playwright/test';

const isTouchProject = () => ['mobile', 'ipad-touch'].includes(test.info().project.name);

async function expectRevealed(locator: Locator, revealed: boolean, message: string) {
  await expect.poll(
    () => locator.evaluate((node) => getComputedStyle(node).opacity),
    { message },
  ).toBe(revealed ? '1' : '0');
}

async function expectPointerActivation(locator: Locator, activatable: boolean, message: string) {
  await expect.poll(
    () => locator.evaluate((node) => getComputedStyle(node).pointerEvents),
    { message },
  ).toBe(activatable ? 'auto' : 'none');
}

test('book-card actions keep a shared baseline for touch, mouse, and keyboard', async ({ page }) => {
  await page.goto('/app');

  const details = page.locator('a[aria-label^="Open details for"]');
  await expect(details.first()).toBeVisible();
  expect(await details.count(), 'the catalog fixture needs at least two books').toBeGreaterThan(1);

  const firstCard = details.nth(0).locator('..');
  const secondCard = details.nth(1).locator('..');
  const firstTitle = details.nth(0).locator('p').first();
  const secondTitle = details.nth(1).locator('p').first();
  const firstRead = firstCard.locator('a[aria-label^="Read "]');
  const secondRead = secondCard.locator('a[aria-label^="Read "]');

  await expect(firstRead).toHaveCount(1);
  await expect(secondRead).toHaveCount(1);

  // Deterministic reporter data shape: one one-line title beside one title that
  // reaches the two-line clamp. This changes fixture text only; the production
  // BookCard layout and media-query behavior remain untouched.
  await firstTitle.evaluate((node) => { node.textContent = 'Short'; });
  await secondTitle.evaluate((node) => {
    node.textContent = 'A deliberately long title that must occupy the complete two-line card-title allowance';
  });

  for (const theme of ['light', 'dark'] as const) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    await page.waitForTimeout(250);

    const titleHeights = await Promise.all([
      firstTitle.evaluate((node) => node.getBoundingClientRect().height),
      secondTitle.evaluate((node) => node.getBoundingClientRect().height),
    ]);
    expect(
      Math.abs(titleHeights[0] - titleHeights[1]),
      `${theme}: one- and two-line titles reserve the same two-line block`,
    ).toBeLessThanOrEqual(1);

    const actionBottoms = await Promise.all([
      firstRead.evaluate((node) => node.getBoundingClientRect().bottom),
      secondRead.evaluate((node) => node.getBoundingClientRect().bottom),
    ]);
    expect(
      Math.abs(actionBottoms[0] - actionBottoms[1]),
      `${theme}: Read now actions share a bottom baseline`,
    ).toBeLessThanOrEqual(1);

    if (!isTouchProject()) {
      await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
      await page.mouse.move(0, 0);
      await expectRevealed(firstRead, false, `${theme}: desktop starts with the clean hover treatment`);
      await firstCard.hover();
      await expectRevealed(firstRead, true, `${theme}: mouse hover reveals Read now`);
      await page.mouse.move(0, 0);
      await firstRead.focus();
      await expectRevealed(firstRead, true, `${theme}: keyboard focus reveals Read now`);
    }
  }
});

test('coarse pointers keep every redundant card action concealed at rest (2026-08-29 ruling)', async ({ page }) => {
  test.skip(!isTouchProject(), 'coarse-pointer resting-state regression');

  await page.goto('/app');
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());

  const catalogControls = [
    ['Edit', page.locator('a[aria-label^="Edit "]')],
    ['Read now', page.locator('a[aria-label^="Read "]')],
  ] as const;

  for (const [name, control] of catalogControls) {
    await expect(control.first(), `${name} action loads with the asynchronous catalog grid`).toBeAttached();
    expect(await control.count(), `the fixture must render at least one ${name} card action`).toBeGreaterThan(0);
    const opaque = await control.evaluateAll((nodes) =>
      nodes.filter((node) => getComputedStyle(node).opacity === '1').length,
    );
    expect(opaque, `${name} controls are concealed at rest on a coarse pointer`).toBe(0);
    const activatable = await control.evaluateAll((nodes) =>
      nodes.filter((node) => getComputedStyle(node).pointerEvents !== 'none').length,
    );
    expect(activatable, `${name} controls reject pointer activation while concealed`).toBe(0);
  }

  const catalogCard = page.locator('[class*="wrap"]').filter({
    has: page.locator('a[aria-label^="Edit "]'),
  }).first();
  const catalogDetails = catalogCard.locator('a[aria-label^="Open details for"]');
  const catalogRead = catalogCard.locator('a[aria-label^="Read "]');
  const catalogEdit = catalogCard.locator('a[aria-label^="Edit "]');
  const restingHeight = await catalogCard.evaluate((node) => node.getBoundingClientRect().height);
  await catalogCard.hover();
  await expectRevealed(catalogRead, true, 'touch context still honors the shared hover reveal');
  await expectRevealed(catalogEdit, true, 'touch context still honors Edit hover reveal');
  await expectPointerActivation(catalogRead, true, 'hover-revealed Read now accepts pointer activation');
  await expectPointerActivation(catalogEdit, true, 'hover-revealed Edit accepts pointer activation');
  await page.mouse.move(0, 0);
  await catalogDetails.focus();
  await expectRevealed(catalogRead, true, 'card focus-within reveals Read now on touch');
  await expectRevealed(catalogEdit, true, 'card focus-within reveals Edit on touch');
  await expectPointerActivation(catalogRead, true, 'focus-within revealed Read now accepts pointer activation');
  await expectPointerActivation(catalogEdit, true, 'focus-within revealed Edit accepts pointer activation');
  const focusedHeight = await catalogCard.evaluate((node) => node.getBoundingClientRect().height);
  expect(Math.abs(focusedHeight - restingHeight),
    'revealing the reserved action row by focus must not reflow the grid').toBeLessThanOrEqual(0.5);
  await page.keyboard.press('Tab');
  await expect(catalogRead, 'keyboard reaches Read now after the card link').toBeFocused();
  await page.keyboard.press('Tab');
  await expect(catalogEdit, 'keyboard reaches Edit after Read now').toBeFocused();
  await expectPointerActivation(catalogEdit, true, 'focused Edit remains pointer-activatable');

  await page.goto('/app');
  const revealedEdit = page.locator('a[aria-label^="Edit "]').first();
  await expect(revealedEdit).toBeAttached();
  await revealedEdit.locator('..').locator('..').hover();
  await expectPointerActivation(revealedEdit, true, 'hover reveals Edit before a real click');
  await revealedEdit.click();
  await expect(page, 'hover-reveal-then-click still opens the edit route').toHaveURL(/\/app\/book\/\d+\/edit$/);

  // The default E2E admin uses universal-library mode, where Catalog has no
  // per-card removal action. Exercise the same real BookCard X on a temporary
  // shelf instead of weakening the regression into a synthetic DOM fixture.
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const { csrf_token } = await csrf.json() as { csrf_token: string };
  const headers = { 'X-CSRFToken': csrf_token };
  const books = await page.request.get('/api/v1/books?per_page=1');
  const { items } = await books.json() as { items: Array<{ id: number }> };
  expect(items.length, 'the fixture must contain a book for the removal-card probe').toBeGreaterThan(0);

  const created = await page.request.post('/api/v1/shelves', {
    headers,
    data: { name: `touch-card-actions-${Date.now()}` },
  });
  expect(created.ok(), 'temporary shelf creation').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;
  try {
    const added = await page.request.post(`/api/v1/shelves/${shelfId}/books/${items[0].id}`, { headers });
    expect(added.ok(), 'temporary shelf membership').toBeTruthy();
    await page.goto(`/app/shelf/${shelfId}`);
    const remove = page.getByRole('button', { name: 'Remove from shelf' });
    await expect(remove).toHaveCount(1);
    await expectRevealed(remove, false, 'Remove controls are concealed at rest on a coarse pointer');
    await expectPointerActivation(remove, false, 'concealed Remove rejects pointer activation');

    const restingTarget = await remove.boundingBox();
    expect(restingTarget, 'concealed Remove keeps its coarse-pointer box in layout').not.toBeNull();
    const removalResponse = page.waitForResponse((response) =>
      response.url().includes(`/api/v1/shelves/${shelfId}/books/${items[0].id}/delete`)
        && response.request().method() === 'POST',
    { timeout: 750 }).then(() => true).catch(() => false);
    await page.touchscreen.tap(
      restingTarget!.x + restingTarget!.width / 2,
      restingTarget!.y + restingTarget!.height / 2,
    );
    expect(await removalResponse, 'a tap on concealed Remove must not fire its destructive request').toBeFalsy();
    const shelfAfterRestingTap = await page.request.get(`/api/v1/shelves/${shelfId}?per_page=10`);
    expect(shelfAfterRestingTap.ok(), 'temporary shelf remains readable after the resting tap').toBeTruthy();
    const shelfItems = ((await shelfAfterRestingTap.json()) as { items: Array<{ id: number }> }).items;
    expect(shelfItems.some(({ id }) => id === items[0].id),
      'the book remains on the shelf after tapping concealed Remove').toBeTruthy();

    await page.goto(`/app/shelf/${shelfId}`);
    const shelfCard = remove.locator('..');
    const shelfDetails = shelfCard.locator('a[aria-label^="Open details for"]');
    await shelfCard.hover();
    await expectRevealed(remove, true, 'touch context still honors Remove hover reveal');
    await expectPointerActivation(remove, true, 'hover-revealed Remove accepts pointer activation');
    await page.mouse.move(0, 0);
    await shelfDetails.focus();
    await expectRevealed(remove, true, 'card focus-within reveals Remove on touch');
    await expectPointerActivation(remove, true, 'focus-within revealed Remove accepts pointer activation');
    await page.keyboard.press('Tab');
    await expect(remove, 'keyboard reaches Remove after the shelf card link').toBeFocused();
    await expectRevealed(remove, true, 'Remove remains revealed at keyboard focus');
    await expectPointerActivation(remove, true, 'focused Remove remains pointer-activatable');
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => undefined);
  }
});

test('quick-edit pencil uses the light-theme card-surface palette', async ({ page }) => {
  await page.goto('/app');
  const quickEdit = page.locator('a[aria-label^="Edit "]').first();
  await expect(quickEdit).toHaveCount(1);

  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  if (isTouchProject()) {
    // 2026-08-29 ruling: touch actions are concealed at rest, so reveal this
    // one with the same keyboard focus path whose palette users will see.
    await quickEdit.focus();
  } else {
    await quickEdit.locator('..').locator('..').locator('a[aria-label^="Open details for"]').hover();
  }
  await expectRevealed(quickEdit, true, 'light: quick edit is revealed before its visible palette is measured');

  const expected = await quickEdit.evaluate(() => {
    const resolveToken = (token: string) => {
      const probe = document.createElement('span');
      probe.style.color = `var(${token})`;
      document.body.appendChild(probe);
      const resolved = getComputedStyle(probe).color;
      probe.remove();
      return resolved;
    };
    return {
      background: resolveToken('--surface-2'),
      color: resolveToken('--text-muted'),
      border: resolveToken('--border'),
    };
  });

  await expect.poll(() => quickEdit.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      background: style.backgroundColor,
      color: style.color,
      border: style.borderColor,
    };
  }), { message: 'light: quick edit resolves to the on-surface palette' }).toEqual(expected);
});
