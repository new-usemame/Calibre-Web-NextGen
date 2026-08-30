import { test, expect, type Locator } from '@playwright/test';

const isTouchProject = () => test.info().project.use.hasTouch === true;

async function tap(locator: Locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  expect(box, 'a real touch target needs a rendered hit-test box').not.toBeNull();
  await test.info().attach('touch-target', {
    body: JSON.stringify(box),
    contentType: 'application/json',
  });
  await locator.page().touchscreen.tap(
    box!.x + box!.width / 2,
    box!.y + box!.height / 2,
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

test('coarse pointers use a visible actions disclosure with real touch input', async ({ page }) => {
  test.skip(!isTouchProject(), 'coarse-pointer actions disclosure');

  await page.goto('/app');
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());

  const catalogCard = page.locator('[class*="wrap"]').filter({
    has: page.locator('a[aria-label^="Edit "]'),
  }).first();
  const catalogRead = catalogCard.locator('a[aria-label^="Read "]');
  const catalogEdit = catalogCard.locator('a[aria-label^="Edit "]');
  await expect(catalogRead, 'the catalog fixture needs a readable book').toBeAttached();
  await expect(catalogEdit, 'the catalog fixture needs an editable book').toBeAttached();

  // Broken-state discriminator. Before the disclosure fix these links had
  // opacity:0 but still owned real hit-test boxes. A genuine touch tap at the
  // blank-looking Read box navigated iPad-class WebKit and Chromium straight
  // into the reader. The fixed coarse-pointer layout removes those legacy
  // controls from layout and hit testing, so there is no box to tap.
  const invisibleReadBox = await catalogRead.boundingBox();
  if (invisibleReadBox) {
    const before = page.url();
    await page.touchscreen.tap(
      invisibleReadBox.x + invisibleReadBox.width / 2,
      invisibleReadBox.y + invisibleReadBox.height / 2,
    );
    await page.waitForTimeout(300);
    expect(page.url(), 'blank card space must never activate an invisible Read link').toBe(before);
  }

  await expect(catalogRead, 'legacy Read is absent from the coarse-pointer layout').toBeHidden();
  await expect(catalogEdit, 'legacy Edit is absent from the coarse-pointer layout').toBeHidden();

  const more = catalogCard.getByRole('button', { name: /^More actions for / });
  await expect(more).toBeVisible();
  const target = await more.boundingBox();
  expect(target!.width, 'More actions touch target width').toBeGreaterThanOrEqual(44);
  expect(target!.height, 'More actions touch target height').toBeGreaterThanOrEqual(44);
  await expect(more).toHaveAttribute('aria-expanded', 'false');

  // TOUCH: the first tap reveals labelled actions without navigating.
  const catalogUrl = page.url();
  await tap(more);
  await expect(page).toHaveURL(catalogUrl);
  await expect(more).toHaveAttribute('aria-expanded', 'true');
  const actions = catalogCard.getByRole('group', { name: /^Actions for / });
  const readAction = actions.getByRole('link', { name: /^Read / });
  const editAction = actions.getByRole('link', { name: /^Edit / });
  await expect(readAction).toBeVisible();
  await expect(editAction).toBeVisible();

  // KEYBOARD: disclosure state is announced, Tab reaches its ordinary links,
  // and Escape closes it while restoring focus to the trigger.
  await page.keyboard.press('Escape');
  await expect(more).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(more).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Tab');
  await expect(readAction).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(more).toBeFocused();

  // MOUSE on a hybrid coarse-pointer device: the same disclosure is clickable.
  await more.click();
  await expect(editAction).toBeVisible();
  await page.keyboard.press('Escape');

  // TOUCH: activate the real Read action from the visible disclosure.
  await tap(more);
  await tap(readAction);
  await expect(page).toHaveURL(/\/app\/read\/\d+/);
  await page.goBack();
  await expect(catalogCard).toBeVisible();

  // Horizontal rails are overflow containers, which otherwise clip an
  // absolutely-positioned panel on the block axis. Exercise a real Discover
  // BookCard and assert the disclosed Read link is the element hit at its own
  // centre, not merely present behind the rail's clipping layer.
  const discover = page.getByTestId('discover-section');
  await expect(discover).toBeVisible();
  const railMore = discover.getByRole('button', { name: /^More actions for / }).first();
  await tap(railMore);
  const railRead = discover
    .getByRole('group', { name: /^Actions for / })
    .getByRole('link', { name: /^Read / });
  await expect(railRead).toBeVisible();
  await railRead.scrollIntoViewIfNeeded();
  expect(await railRead.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return hit === node || node.contains(hit);
  }), 'Discover disclosure action is not clipped or covered at its centre').toBe(true);
  await page.keyboard.press('Escape');

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
    const shelfCard = remove.locator('..');
    await expect(remove, 'legacy Remove is absent from the coarse-pointer layout').toBeHidden();
    const shelfMore = shelfCard.getByRole('button', { name: /^More actions for / });
    await tap(shelfMore);
    const shelfActions = shelfCard.getByRole('group', { name: /^Actions for / });
    const removeAction = shelfActions.getByRole('button', { name: 'Remove from shelf' });
    await expect(removeAction).toBeVisible();
    await tap(removeAction);
    await expect(page.getByRole('button', { name: 'Remove from shelf' })).toHaveCount(0);
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => undefined);
  }
});

test('quick-edit action uses the light-theme palette in both presentations', async ({ page }) => {
  await page.goto('/app');
  const quickEdit = page.locator('a[aria-label^="Edit "]').first();
  await expect(quickEdit).toHaveCount(1);

  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  if (isTouchProject()) {
    // Touch quick-edit disclosure: measure the visible presentation users now
    // invoke, not the deliberately removed pencil that remains in the DOM for
    // fine-pointer CSS. This preserves palette coverage at the real control.
    const card = quickEdit.locator('..').locator('..');
    await tap(card.getByRole('button', { name: /^More actions for / }));
    const panel = card.getByRole('group', { name: /^Actions for / });
    const editAction = panel.getByRole('link', { name: /^Edit / });
    await expect(editAction).toBeVisible();

    const expected = await editAction.evaluate(() => {
      const resolveToken = (token: string) => {
        const probe = document.createElement('span');
        probe.style.color = `var(${token})`;
        document.body.appendChild(probe);
        const resolved = getComputedStyle(probe).color;
        probe.remove();
        return resolved;
      };
      return {
        panelBackground: resolveToken('--surface-2'),
        panelBorder: resolveToken('--border-strong'),
        actionColor: resolveToken('--text'),
      };
    });

    await expect.poll(async () => {
      const panelStyle = await panel.evaluate((node) => {
        const style = getComputedStyle(node);
        return { background: style.backgroundColor, border: style.borderColor };
      });
      const actionColor = await editAction.evaluate((node) => getComputedStyle(node).color);
      return {
        panelBackground: panelStyle.background,
        panelBorder: panelStyle.border,
        actionColor,
      };
    }, { message: 'light: touch quick edit resolves to the disclosure palette' }).toEqual(expected);
    return;
  }

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
