import { test, expect } from '@playwright/test';

test('Edit from the cover action dialog opens the editor directly', async ({ page }) => {
  const touch = test.info().project.use.hasTouch === true;
  await page.setViewportSize(touch ? { width: 375, height: 812 } : { width: 1280, height: 800 });
  await page.goto('/app');

  const details = page.getByTestId('catalog-grid').locator('a[aria-label^="Open details for"]').first();
  await expect(details).toBeVisible();
  const bookId = (await details.getAttribute('href'))!.match(/\/book\/(\d+)/)![1];
  const card = details.locator('..');
  const trigger = card.getByRole('button', { name: /^Actions for / });

  if (touch) await trigger.tap();
  else {
    await details.hover();
    await trigger.focus();
    await trigger.press('Enter');
  }
  const dialog = page.getByRole('dialog', { name: /^Actions for / });
  await expect(dialog).toBeVisible();

  const edit = dialog.getByRole('link', { name: 'Edit', exact: true });
  await expect(edit, 'edit permission exposes a direct editor link').toBeVisible();
  await expect(edit).toHaveAttribute('href', `/app/book/${bookId}/edit`);
  if (touch) await edit.tap();
  else await edit.click();
  await expect(page).toHaveURL(new RegExp(`/book/${bookId}/edit$`));
});

test('removing a shelf card through its action dialog returns keyboard focus to the shelf heading', async ({ page }) => {
  const touch = test.info().project.use.hasTouch === true;
  await page.setViewportSize(touch ? { width: 375, height: 812 } : { width: 1280, height: 800 });
  const csrfResponse = await page.request.get('/api/v1/auth/csrf');
  expect(csrfResponse.ok()).toBeTruthy();
  const { csrf_token } = await csrfResponse.json() as { csrf_token: string };
  const headers = { 'X-CSRFToken': csrf_token };
  const created = await page.request.post('/api/v1/shelves', {
    headers,
    data: { name: `cover-action-${Date.now()}` },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;

  try {
    await page.goto('/app');
    const details = page.getByTestId('catalog-grid').locator('a[aria-label^="Open details for"]').first();
    await expect(details).toBeVisible();
    const title = (await details.getAttribute('aria-label'))!.replace(/^Open details for /, '');
    const bookId = (await details.getAttribute('href'))!.match(/\/book\/(\d+)/)![1];
    const added = await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, { headers });
    expect(added.ok(), await added.text()).toBeTruthy();

    await page.goto(`/app/shelf/${shelfId}`);
    const card = page.locator('[class*="grid"] a[aria-label^="Open details for"]').first().locator('..');
    await expect(card).toBeVisible();
    const trigger = card.getByRole('button', { name: /^Actions for / });
    if (touch) await trigger.tap();
    else {
      await trigger.focus();
      await trigger.press('Enter');
    }
    const dialog = page.getByRole('dialog', { name: /^Actions for / });
    await expect(dialog).toBeVisible();

    const remove = dialog.getByRole('button', { name: 'Remove from shelf', exact: true });
    await expect(remove).toBeVisible();
    const removed = page.waitForResponse(response =>
      response.url().endsWith(`/api/v1/shelves/${shelfId}/books/${bookId}/delete`)
      && response.request().method() === 'POST');
    if (touch) await remove.tap();
    else {
      await remove.focus();
      await remove.press('Enter');
    }
    expect((await removed).ok()).toBeTruthy();
    await expect(page.getByRole('link', { name: `Open details for ${title}`, exact: true })).toHaveCount(0);
    await expect(page.getByTestId('shelf-heading')).toBeFocused();
    const membership = await page.request.get(`/api/v1/shelves/${shelfId}`);
    expect(membership.ok()).toBeTruthy();
    expect(((await membership.json()) as { items: { id: number }[] }).items.map(book => book.id)).not.toContain(Number(bookId));
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => undefined);
  }
});


test('cover Edit remains a real link that opens in a new tab', async ({ page }) => {
  test.skip(test.info().project.use.hasTouch === true, 'middle-click is a mouse affordance');
  await page.goto('/app');
  const details = page.getByTestId('catalog-grid').locator('a[aria-label^="Open details for"]').first();
  await expect(details).toBeVisible();
  const id = (await details.getAttribute('href'))!.match(/\/book\/(\d+)/)![1];
  await details.hover();
  await details.locator('..').getByRole('button', { name: /^Actions for / }).click();
  const edit = page.getByRole('dialog').getByRole('link', { name: 'Edit', exact: true });
  // A native middle-click opens a background tab, which need not carry
  // window.opener or emit this page's popup event. Observe the browser context.
  const opened = page.context().waitForEvent('page');
  await edit.click({ button: 'middle' });
  const editor = await opened;
  try { await expect(editor).toHaveURL(new RegExp(`/app/book/${id}/edit$`)); }
  finally { await editor.close(); }
  await expect(page).toHaveURL(/\/app\/?$/);
});
