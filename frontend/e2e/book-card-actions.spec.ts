import { test, expect, type Locator } from '@playwright/test';

const isTouchProject = () => test.info().project.use.hasTouch === true;

async function tap(locator: Locator) {
  let target: Awaited<ReturnType<Locator['boundingBox']>> = null;
  await expect.poll(async () => {
    try {
      await locator.scrollIntoViewIfNeeded();
      const box = await locator.boundingBox();
      target = box;
      return box !== null;
    } catch {
      // Query refetches can replace a memoized card between locator resolution
      // and geometry sampling. Re-resolve the same locator; the eventual input
      // remains a genuine coordinate touch, not Playwright's mouse click path.
      target = null;
      return false;
    }
  }, { message: 'a real touch target must stay attached long enough to sample' }).toBe(true);
  await test.info().attach('touch-target', {
    body: JSON.stringify(target),
    contentType: 'application/json',
  });
  await locator.page().touchscreen.tap(
    target!.x + target!.width / 2,
    target!.y + target!.height / 2,
  );
}

async function expectRevealed(locator: Locator, revealed: boolean, message: string) {
  await expect.poll(
    () => locator.evaluate((node) => getComputedStyle(node).opacity),
    { message },
  ).toBe(revealed ? '1' : '0');
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

    if (!isTouchProject()) {
      const actionBottoms = await Promise.all([
        firstRead.evaluate((node) => node.getBoundingClientRect().bottom),
        secondRead.evaluate((node) => node.getBoundingClientRect().bottom),
      ]);
      expect(
        Math.abs(actionBottoms[0] - actionBottoms[1]),
        `${theme}: Read now actions share a bottom baseline`,
      ).toBeLessThanOrEqual(1);

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

test('coarse pointers carry no card actions; the book page owns them', async ({ page }) => {
  test.skip(!isTouchProject(), 'coarse-pointer card layout');

  await page.goto('/app');
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());

  // Wait for real cards first: `toHaveCount(0)` is satisfied by an empty page,
  // so asserting absence before the grid renders proves nothing.
  const anyCard = page.locator('a[aria-label^="Open details for"]');
  await expect(anyCard.first()).toBeVisible();

  // Operator ruling 2026-09-12: the "…" disclosure on every cover was too much
  // chrome on a phone. A touch card is the cover plus its title link, and the
  // three redundant actions live one tap away on the book's own page.
  await expect(
    page.getByRole('button', { name: /^More actions for / }),
    'no card may carry a More actions disclosure on a coarse pointer',
  ).toHaveCount(0);

  const catalogCard = page.locator('[class*="wrap"]').filter({
    has: page.locator('a[aria-label^="Edit "]'),
  }).first();
  const catalogDetails = catalogCard.locator('a[aria-label^="Open details for"]');
  const catalogRead = catalogCard.locator('a[aria-label^="Read "]');
  const catalogEdit = catalogCard.locator('a[aria-label^="Edit "]');
  await expect(catalogRead, 'the catalog fixture needs a readable book').toBeAttached();
  await expect(catalogEdit, 'the catalog fixture needs an editable book').toBeAttached();

  // PR #2028's invariant still holds and is the reason these keep `display:none`
  // rather than `opacity:0`: an iPad tap applies synthetic hover, so a
  // transparent-but-laid-out control reveals AND activates under the same
  // finger. Removed from layout means there is no box to tap at all.
  expect(
    await catalogRead.boundingBox(),
    'legacy Read must occupy no box in the coarse-pointer layout',
  ).toBeNull();
  expect(
    await catalogEdit.boundingBox(),
    'legacy Edit must occupy no box in the coarse-pointer layout',
  ).toBeNull();

  // TOUCH: the only thing a tap on the card can do is open the book.
  const href = await catalogDetails.getAttribute('href');
  const bookId = href!.match(/\/book\/(\d+)$/)![1];
  await tap(catalogDetails);
  await expect(page).toHaveURL(new RegExp(`/book/${bookId}$`));

  // …and the book page carries the actions the card gave up.
  const detailRead = page.getByRole('link', { name: 'Read now' });
  await expect(detailRead, 'the book page offers Read now').toBeVisible();
  // Edit lives in the "More actions" gear menu now; a tap opens it and the
  // menuitem navigates to the editor. Wait for the menu itself before
  // asserting its items — WebKit's synthetic tap resolves a beat later.
  await tap(page.getByTestId('book-actions-menu'));
  const menuList = page.getByTestId('book-actions-menu-list');
  await expect(menuList).toBeVisible({ timeout: 10_000 });
  const editItem = menuList.getByRole('menuitem', { name: 'Edit metadata' });
  await expect(editItem, 'the book page offers Edit metadata in the gear menu').toBeVisible();
  await tap(editItem);
  await expect(page).toHaveURL(new RegExp(`/book/${bookId}/edit$`));
  await page.goBack();
  await expect(page.getByRole('link', { name: 'Read now' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Add to shelf' }),
    'the book page owns shelf membership, which is how a shelf removal is reached',
  ).toBeVisible();

  await tap(detailRead);
  // EPUB/KEPUB open the epub.js reader (/read/<id>); the other readable
  // formats go to the native reader (/view/<id>/<format>). The Recent default
  // sort (#2238) decides which book leads the catalog, so pin the behaviour —
  // "Read now opens this book's reader" — not one route shape.
  await expect(page).toHaveURL(new RegExp(`/app/(read|view)/${bookId}\\b`));
  await page.goBack();

  // A shelf card is the same story: its X is gone on touch, and removing the
  // book from the shelf is a toggle inside the book page's Add-to-shelf popover.
  const csrf = await page.request.get('/api/v1/auth/csrf');
  const { csrf_token } = await csrf.json() as { csrf_token: string };
  const headers = { 'X-CSRFToken': csrf_token };
  const created = await page.request.post('/api/v1/shelves', {
    headers,
    data: { name: `touch-card-actions-${Date.now()}` },
  });
  expect(created.ok(), 'temporary shelf creation').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;
  const shelfName = ((await page.request.get(`/api/v1/shelves/${shelfId}`)).ok())
    ? ((await (await page.request.get(`/api/v1/shelves/${shelfId}`)).json()) as { name: string }).name
    : '';
  try {
    const added = await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, { headers });
    expect(added.ok(), 'temporary shelf membership').toBeTruthy();
    await page.goto(`/app/shelf/${shelfId}`);
    const remove = page.getByRole('button', { name: 'Remove from shelf', includeHidden: true });
    await expect(remove).toHaveCount(1);
    expect(
      await remove.boundingBox(),
      'legacy Remove must occupy no box in the coarse-pointer layout',
    ).toBeNull();
    await expect(
      page.getByRole('button', { name: /^More actions for / }),
      'a shelf card carries no disclosure either',
    ).toHaveCount(0);

    // The reachable path: open the book, toggle the shelf off.
    await page.goto(`/app/book/${bookId}`);
    await page.getByRole('button', { name: 'Add to shelf' }).click();
    await page.getByRole('button', { name: shelfName }).click();
    await page.goto(`/app/shelf/${shelfId}`);
    await expect(
      page.getByRole('button', { name: 'Remove from shelf', includeHidden: true }),
      'the book left the shelf through the book page',
    ).toHaveCount(0);
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => undefined);
  }
});

test('quick-edit action uses the light-theme palette in both presentations', async ({ page }) => {
  // The pencil is display:none on coarse pointers — the operator's 2026-09-12
  // ruling leaves a touch card with no actions at all — so there is no visible
  // control here to measure. Touch reachability is covered by the test above.
  test.skip(isTouchProject(), 'fine-pointer quick-edit palette');
  await page.goto('/app');
  const quickEdit = page.locator('a[aria-label^="Edit "]').first();
  await expect(quickEdit).toHaveCount(1);

  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await quickEdit.locator('..').locator('..').locator('a[aria-label^="Open details for"]').hover();
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
